//! JSON-RPC 2.0 over newline-delimited UTF-8 JSON, as spoken by `mynotes serve --stdio`.
//!
//! Framing rules (shared with the Python server, see ARCHITECTURE.md section A1):
//! - one JSON message per line, terminated by `\n` (a trailing `\r` is tolerated);
//! - at most [`MAX_MESSAGE_BYTES`] per message, newline excluded;
//! - no batches;
//! - the server sends responses (with `id`) and notifications (with `method`, no `id`).
//!
//! This module is pure: it never touches a process or a socket, so every rule here is
//! covered by unit tests at the bottom of the file.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Protocol version negotiated in `system.hello`.
pub const PROTOCOL_VERSION: u32 = 1;

/// Hard upper bound for one framed message (16 MiB), mirrored by the server.
pub const MAX_MESSAGE_BYTES: usize = 16 * 1024 * 1024;

/// Standard and MyNotes-specific JSON-RPC error codes.
pub mod codes {
    pub const PARSE_ERROR: i64 = -32700;
    pub const INVALID_REQUEST: i64 = -32600;
    pub const METHOD_NOT_FOUND: i64 = -32601;
    pub const INVALID_PARAMS: i64 = -32602;
    pub const INTERNAL_ERROR: i64 = -32603;
    /// The CLI printed `mynotes: …` (a plain-text error).
    pub const CLI_ERROR: i64 = -32001;
    /// The workspace or a lane is busy.
    pub const BUSY: i64 = -32002;
    /// The command finished but needs the student's review.
    pub const NEEDS_REVIEW: i64 = -32003;
    /// App-side: the backend restarted while the call was pending.
    pub const BACKEND_RESTARTED: i64 = -32010;
    /// App-side: no backend process is connected.
    pub const BACKEND_UNAVAILABLE: i64 = -32011;
    /// App-side: the webview asked for a method outside the allowlist.
    pub const METHOD_NOT_ALLOWED: i64 = -32012;
}

const JSONRPC: &str = "2.0";

/// A request or response identifier. The app always sends numbers; strings are accepted.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(untagged)]
pub enum RequestId {
    Number(i64),
    String(String),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Request {
    pub jsonrpc: String,
    pub id: RequestId,
    pub method: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub params: Option<Value>,
}

impl Request {
    pub fn new(id: i64, method: impl Into<String>, params: Option<Value>) -> Self {
        Self { jsonrpc: JSONRPC.to_owned(), id: RequestId::Number(id), method: method.into(), params }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Notification {
    pub jsonrpc: String,
    pub method: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub params: Option<Value>,
}

impl Notification {
    pub fn new(method: impl Into<String>, params: Option<Value>) -> Self {
        Self { jsonrpc: JSONRPC.to_owned(), method: method.into(), params }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RpcError {
    pub code: i64,
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl RpcError {
    pub fn new(code: i64, message: impl Into<String>) -> Self {
        Self { code, message: message.into(), data: None }
    }
}

/// A response: exactly one of `result` or `error` is present.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Response {
    pub jsonrpc: String,
    /// `null` only when the server could not read the request id (parse error).
    pub id: Option<RequestId>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<RpcError>,
}

impl Response {
    /// Collapse the response into a `Result`, the shape the supervisor hands to callers.
    pub fn into_result(self) -> Result<Value, RpcError> {
        match (self.result, self.error) {
            (_, Some(error)) => Err(error),
            (Some(result), None) => Ok(result),
            (None, None) => Ok(Value::Null),
        }
    }
}

/// What the app can receive from the server.
#[derive(Debug, Clone, PartialEq)]
pub enum Incoming {
    Response(Response),
    Notification(Notification),
}

#[derive(Debug, Clone, thiserror::Error, PartialEq)]
pub enum ProtocolError {
    #[error("message is {size} bytes, above the {limit}-byte limit")]
    TooLarge { size: usize, limit: usize },
    #[error("message is not valid UTF-8")]
    NotUtf8,
    #[error("message is not valid JSON-RPC: {0}")]
    Invalid(String),
    #[error("backend speaks protocol {found}, the app needs {expected}")]
    VersionMismatch { expected: u32, found: u32 },
}

/// Serialize one message and terminate it with `\n`.
///
/// `serde_json` never emits a raw newline in compact mode (newlines inside strings are
/// escaped), so the output is always exactly one line.
pub fn encode<T: Serialize>(message: &T) -> Result<Vec<u8>, ProtocolError> {
    let mut bytes = serde_json::to_vec(message).map_err(|error| ProtocolError::Invalid(error.to_string()))?;
    if bytes.len() > MAX_MESSAGE_BYTES {
        return Err(ProtocolError::TooLarge { size: bytes.len(), limit: MAX_MESSAGE_BYTES });
    }
    bytes.push(b'\n');
    Ok(bytes)
}

/// Parse one framed line (without its newline) coming from the server.
pub fn parse_incoming(line: &[u8]) -> Result<Incoming, ProtocolError> {
    if line.len() > MAX_MESSAGE_BYTES {
        return Err(ProtocolError::TooLarge { size: line.len(), limit: MAX_MESSAGE_BYTES });
    }
    let text = std::str::from_utf8(line).map_err(|_| ProtocolError::NotUtf8)?;
    let value: Value = serde_json::from_str(text).map_err(|error| ProtocolError::Invalid(error.to_string()))?;
    let object = value
        .as_object()
        .ok_or_else(|| ProtocolError::Invalid("expected an object (batches are not supported)".into()))?;
    if object.get("jsonrpc").and_then(Value::as_str) != Some(JSONRPC) {
        return Err(ProtocolError::Invalid("missing \"jsonrpc\": \"2.0\"".into()));
    }
    let has_method = object.contains_key("method");
    let has_id = object.contains_key("id");
    if has_method && !has_id {
        return serde_json::from_value(value)
            .map(Incoming::Notification)
            .map_err(|error| ProtocolError::Invalid(error.to_string()));
    }
    if has_id && !has_method {
        let response: Response =
            serde_json::from_value(value).map_err(|error| ProtocolError::Invalid(error.to_string()))?;
        if response.result.is_some() && response.error.is_some() {
            return Err(ProtocolError::Invalid("response carries both result and error".into()));
        }
        return Ok(Incoming::Response(response));
    }
    Err(ProtocolError::Invalid("server-to-client requests are not supported".into()))
}

/// Splits a byte stream into complete lines.
///
/// Bytes are buffered until a `\n` arrives, so multi-byte UTF-8 sequences split across reads
/// are reassembled. A line longer than [`MAX_MESSAGE_BYTES`] yields one `TooLarge` error and is
/// discarded up to its newline, after which decoding resumes normally.
#[derive(Debug)]
pub struct LineDecoder {
    buffer: Vec<u8>,
    limit: usize,
    discarding: Option<usize>,
}

impl Default for LineDecoder {
    fn default() -> Self {
        Self::with_limit(MAX_MESSAGE_BYTES)
    }
}

impl LineDecoder {
    pub fn with_limit(limit: usize) -> Self {
        Self { buffer: Vec::new(), limit, discarding: None }
    }

    /// Feed a chunk; returns every complete, non-empty line it closes.
    pub fn push(&mut self, chunk: &[u8]) -> Vec<Result<Vec<u8>, ProtocolError>> {
        let mut out = Vec::new();
        for &byte in chunk {
            if byte == b'\n' {
                if let Some(size) = self.discarding.take() {
                    out.push(Err(ProtocolError::TooLarge { size, limit: self.limit }));
                    continue;
                }
                let mut line = std::mem::take(&mut self.buffer);
                if line.last() == Some(&b'\r') {
                    line.pop();
                }
                if !line.iter().all(u8::is_ascii_whitespace) {
                    out.push(Ok(line));
                }
                continue;
            }
            if let Some(size) = self.discarding.as_mut() {
                *size += 1;
                continue;
            }
            self.buffer.push(byte);
            // `\r` may still be stripped, so allow one extra byte before giving up.
            if self.buffer.len() > self.limit + 1 {
                self.discarding = Some(self.buffer.len());
                self.buffer = Vec::new();
            }
        }
        out
    }

    /// Bytes waiting for their newline.
    pub fn pending(&self) -> usize {
        self.buffer.len()
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HelloParams {
    pub protocol: u32,
}

/// The parts of `system.hello` the app relies on; other fields pass through untouched.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HelloResult {
    pub protocol: u32,
    #[serde(default)]
    pub methods: Vec<String>,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, Value>,
}

/// Reject a backend that speaks another protocol version.
pub fn check_hello(hello: &HelloResult) -> Result<(), ProtocolError> {
    if hello.protocol == PROTOCOL_VERSION {
        Ok(())
    } else {
        Err(ProtocolError::VersionMismatch { expected: PROTOCOL_VERSION, found: hello.protocol })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn encodes_one_line_with_trailing_newline() {
        let request = Request::new(7, "capture.dashboard", Some(json!({"note": "line one\nline two"})));
        let bytes = encode(&request).unwrap();
        assert_eq!(bytes.last(), Some(&b'\n'));
        assert_eq!(bytes.iter().filter(|&&b| b == b'\n').count(), 1, "inner newlines must be escaped");
        let text = std::str::from_utf8(&bytes).unwrap();
        assert!(text.starts_with(r#"{"jsonrpc":"2.0","id":7,"method":"capture.dashboard""#));
    }

    #[test]
    fn omits_absent_params() {
        let bytes = encode(&Request::new(1, "system.ping", None)).unwrap();
        assert_eq!(bytes, b"{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"system.ping\"}\n");
    }

    #[test]
    fn refuses_to_encode_oversized_messages() {
        let huge = "x".repeat(MAX_MESSAGE_BYTES);
        let error = encode(&Request::new(1, "profile.save", Some(json!({ "background": huge })))).unwrap_err();
        assert!(matches!(error, ProtocolError::TooLarge { .. }));
    }

    #[test]
    fn decoder_reassembles_lines_split_across_chunks() {
        let mut decoder = LineDecoder::default();
        assert!(decoder.push(b"{\"a\":").is_empty());
        assert_eq!(decoder.pending(), 5);
        let lines = decoder.push(b"1}\n{\"b\":2}\n{\"c\"");
        assert_eq!(lines, vec![Ok(b"{\"a\":1}".to_vec()), Ok(b"{\"b\":2}".to_vec())]);
        assert_eq!(decoder.push(b":3}\n"), vec![Ok(b"{\"c\":3}".to_vec())]);
    }

    #[test]
    fn decoder_keeps_utf8_sequences_split_between_reads() {
        let line = "{\"title\":\"Microeconomia · Lezione 5\"}\n".as_bytes();
        let split = line.iter().position(|&b| b == 0xC2).unwrap() + 1; // inside "·"
        let mut decoder = LineDecoder::default();
        assert!(decoder.push(&line[..split]).is_empty());
        let lines = decoder.push(&line[split..]);
        let text = String::from_utf8(lines[0].clone().unwrap()).unwrap();
        assert!(text.contains("Microeconomia · Lezione 5"));
    }

    #[test]
    fn decoder_strips_crlf_and_skips_blank_lines() {
        let mut decoder = LineDecoder::default();
        let lines = decoder.push(b"\n  \r\n{\"ok\":true}\r\n");
        assert_eq!(lines, vec![Ok(b"{\"ok\":true}".to_vec())]);
    }

    #[test]
    fn decoder_reports_an_oversized_line_once_then_recovers() {
        let mut decoder = LineDecoder::with_limit(8);
        let lines = decoder.push(b"0123456789abcdef\n{\"k\":1}\n");
        assert_eq!(lines.len(), 2);
        assert!(matches!(lines[0], Err(ProtocolError::TooLarge { limit: 8, .. })));
        assert_eq!(lines[1], Ok(b"{\"k\":1}".to_vec()));
    }

    #[test]
    fn parses_a_successful_response() {
        let incoming =
            parse_incoming(br#"{"jsonrpc":"2.0","id":3,"result":{"exitCode":0,"data":{"status":"OK"}}}"#).unwrap();
        let Incoming::Response(response) = incoming else { panic!("expected a response") };
        assert_eq!(response.id, Some(RequestId::Number(3)));
        assert_eq!(response.into_result().unwrap(), json!({"exitCode": 0, "data": {"status": "OK"}}));
    }

    #[test]
    fn parses_an_error_response_with_data() {
        let line = br#"{"jsonrpc":"2.0","id":"a1","error":{"code":-32002,"message":"busy","data":{"retryAfter":2}}}"#;
        let Incoming::Response(response) = parse_incoming(line).unwrap() else { panic!("expected a response") };
        assert_eq!(response.id, Some(RequestId::String("a1".into())));
        let error = response.into_result().unwrap_err();
        assert_eq!(error.code, codes::BUSY);
        assert_eq!(error.data, Some(json!({"retryAfter": 2})));
    }

    #[test]
    fn accepts_a_null_id_on_parse_errors() {
        let line = br#"{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}"#;
        let Incoming::Response(response) = parse_incoming(line).unwrap() else { panic!("expected a response") };
        assert_eq!(response.id, None);
    }

    #[test]
    fn parses_notifications() {
        let line = br#"{"jsonrpc":"2.0","method":"job.progress","params":{"jobId":"j1","stage":"GENERATING"}}"#;
        let Incoming::Notification(notification) = parse_incoming(line).unwrap() else {
            panic!("expected a notification")
        };
        assert_eq!(notification.method, "job.progress");
        assert_eq!(notification.params.unwrap()["stage"], "GENERATING");
    }

    #[test]
    fn rejects_batches_wrong_versions_and_server_requests() {
        assert!(matches!(parse_incoming(b"[]"), Err(ProtocolError::Invalid(_))));
        assert!(matches!(parse_incoming(br#"{"jsonrpc":"1.0","id":1,"result":1}"#), Err(ProtocolError::Invalid(_))));
        assert!(matches!(parse_incoming(br#"{"jsonrpc":"2.0","id":1,"method":"x"}"#), Err(ProtocolError::Invalid(_))));
        assert!(matches!(
            parse_incoming(br#"{"jsonrpc":"2.0","id":1,"result":1,"error":{"code":1,"message":"m"}}"#),
            Err(ProtocolError::Invalid(_))
        ));
        assert_eq!(parse_incoming(&[0xff, 0xfe]), Err(ProtocolError::NotUtf8));
    }

    #[test]
    fn hello_rejects_a_mismatched_protocol() {
        let hello: HelloResult =
            serde_json::from_value(json!({"protocol": 1, "methods": ["system.ping"], "version": "0.4.1"})).unwrap();
        assert_eq!(check_hello(&hello), Ok(()));
        assert_eq!(hello.extra["version"], "0.4.1");
        let future = HelloResult { protocol: 2, ..hello };
        assert_eq!(check_hello(&future), Err(ProtocolError::VersionMismatch { expected: 1, found: 2 }));
    }
}
