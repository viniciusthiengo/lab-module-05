"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, clearMemory, forgetNote, getAgents, getHealth, getMemory } from "./api";
import type { Agent, Health, MemoryNote } from "./types";

interface ServerState {
  health: Health | null;
  agents: Agent[];
  memory: MemoryNote[];
  error: string | null;
}

/** The server's status, its agents and its memory, refreshed after every task. */
export function useServer() {
  const [state, setState] = useState<ServerState>({
    health: null,
    agents: [],
    memory: [],
    error: null,
  });

  useEffect(() => {
    let active = true;
    void load().then((update) => {
      if (active) setState((current) => ({ ...current, ...update }));
    });
    return () => {
      active = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    const update = await load();
    setState((current) => ({ ...current, ...update }));
  }, []);

  const forget = useCallback(
    async (id: string) => {
      await forgetNote(id);
      await refresh();
    },
    [refresh],
  );

  const forgetAll = useCallback(async () => {
    await clearMemory();
    await refresh();
  }, [refresh]);

  return { ...state, refresh, forget, forgetAll };
}

/** The server's state, or the error that kept it from loading (the last state is kept). */
async function load(): Promise<Partial<ServerState>> {
  try {
    const [health, agents, memory] = await Promise.all([getHealth(), getAgents(), getMemory()]);
    return { health, agents, memory, error: null };
  } catch (failure) {
    return {
      error: failure instanceof ApiError ? failure.message : "Could not reach the server.",
    };
  }
}
