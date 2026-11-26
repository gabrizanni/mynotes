/** Lessons, recordings and transcripts (`library.list`, pushed by `library.changed`). */
import type { AudioImportItem, Library } from "../../rpc";
import { failed, idle, loading, ready, type Load } from "../load";
import { attempt } from "../result";
import type { SliceContext } from "../types";

export interface LibraryState {
  readonly load: Load;
  readonly library: Library | null;
}

export const libraryInitial: LibraryState = { load: idle, library: null };

export function libraryActions(ctx: SliceContext<"library">) {
  return {
    apply: (library: Library) => {
      ctx.set({ library, load: ready });
    },
    refresh: async () => {
      ctx.set({ load: ctx.get().library ? ctx.get().load : loading });
      try {
        const { data } = await ctx.rpc.call("library.list");
        ctx.set({ library: data, load: ready });
      } catch (error) {
        ctx.set({ load: failed(error) });
      }
    },
    inspectAudio: (paths: readonly string[]) => attempt(async () => (await ctx.rpc.call("library.inspectAudio", { paths: [...paths] })).data.files),
    importAudio: (items: readonly AudioImportItem[]) => attempt(async () => (await ctx.rpc.call("library.importAudio", { items: [...items] })).data),
    verifyFiles: () => attempt(async () => (await ctx.rpc.call("library.verify")).data),
    assignCourse: (recording: string, course: string, lesson?: number) =>
      attempt(async () => (await ctx.rpc.call("library.associate", lesson ? { recordingId: recording, course, lesson } : { recordingId: recording, course })).data),
  };
}
