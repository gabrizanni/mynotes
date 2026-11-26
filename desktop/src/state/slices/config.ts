/** Private configuration (`config.get`, pushed by `config.changed`). */
import type { Config } from "../../rpc";
import { failed, idle, loading, ready, type Load } from "../load";
import type { SliceContext } from "../types";

export interface ConfigState {
  readonly load: Load;
  readonly config: Config | null;
}

export const configInitial: ConfigState = { load: idle, config: null };

export function configActions(ctx: SliceContext<"config">) {
  return {
    apply: (config: Config) => {
      ctx.set({ config, load: ready });
    },
    refresh: async () => {
      ctx.set({ load: ctx.get().config ? ctx.get().load : loading });
      try {
        const { data } = await ctx.rpc.call("config.get");
        ctx.set({ config: data, load: ready });
      } catch (error) {
        ctx.set({ load: failed(error) });
      }
    },
  };
}
