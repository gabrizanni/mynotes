import type { ReactNode } from "react";

import { cx } from "../../design/cx";
import styles from "./Table.module.css";

export interface TableColumn<Row> {
  key: string;
  header: ReactNode;
  cell: (row: Row) => ReactNode;
  align?: "start" | "end";
  /** Marks the column whose cell names the row (rendered as a row header). */
  rowHeader?: boolean;
}

export interface TableProps<Row> {
  /** Accessible caption; visually hidden unless `showCaption`. */
  caption: string;
  showCaption?: boolean;
  columns: readonly TableColumn<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  /** Enables a checkbox column (multi-select, e.g. "Generate notes for 3 lessons"). */
  selection?: {
    selected: ReadonlySet<string>;
    onChange: (selected: Set<string>) => void;
    rowLabel: (row: Row) => string;
    allLabel: string;
  };
}

export function Table<Row>({ caption, showCaption = false, columns, rows, rowKey, selection }: TableProps<Row>) {
  const allSelected = selection !== undefined && rows.length > 0 && rows.every((row) => selection.selected.has(rowKey(row)));
  const someSelected = selection !== undefined && rows.some((row) => selection.selected.has(rowKey(row)));
  const toggle = (key: string) => {
    if (!selection) return;
    const next = new Set(selection.selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    selection.onChange(next);
  };
  return (
    <div className={styles.wrap}>
      <table className={styles.table}>
        <caption className={showCaption ? undefined : "visually-hidden"}>{caption}</caption>
        <thead>
          <tr>
            {selection && (
              <th scope="col" className={styles.check}>
                <input
                  type="checkbox"
                  className={styles.checkbox}
                  aria-label={selection.allLabel}
                  checked={allSelected}
                  ref={(node) => {
                    if (node) node.indeterminate = someSelected && !allSelected;
                  }}
                  onChange={() => selection.onChange(allSelected ? new Set() : new Set(rows.map(rowKey)))}
                />
              </th>
            )}
            {columns.map((column) => (
              <th key={column.key} scope="col" className={cx(column.align === "end" && styles.end)}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const key = rowKey(row);
            const selected = selection?.selected.has(key) ?? false;
            return (
              <tr key={key} className={cx(selected && styles.selected)} aria-selected={selection ? selected : undefined}>
                {selection && (
                  <td className={styles.check}>
                    <input
                      type="checkbox"
                      className={styles.checkbox}
                      aria-label={selection.rowLabel(row)}
                      checked={selected}
                      onChange={() => toggle(key)}
                    />
                  </td>
                )}
                {columns.map((column) =>
                  column.rowHeader ? (
                    <th key={column.key} scope="row" className={cx(column.align === "end" && styles.end)}>
                      {column.cell(row)}
                    </th>
                  ) : (
                    <td key={column.key} className={cx(column.align === "end" && styles.end)}>
                      {column.cell(row)}
                    </td>
                  ),
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
