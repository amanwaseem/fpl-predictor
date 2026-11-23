// Queries more than one page reads, defined once so their keys and options agree.
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api, unwrap } from "./client";

// Every logged model: its gameweeks and role. It needs only the log.
export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: () => unwrap(api.GET("/v1/models")) });
}

export function useScorecard() {
  return useQuery({ queryKey: ["scorecard"], queryFn: () => unwrap(api.GET("/v1/scorecard")) });
}

export function useStatus() {
  return useQuery({ queryKey: ["status"], queryFn: () => unwrap(api.GET("/v1/status")) });
}

// One gameweek: its deadline, whether it is settled, and its fixtures.
export function useGameweek(gameweek: number | null | undefined) {
  return useQuery({
    queryKey: ["gameweek", gameweek],
    enabled: !!gameweek,
    queryFn: () =>
      unwrap(api.GET("/v1/gameweeks/{gameweek}", { params: { path: { gameweek: gameweek! } } })),
  });
}

// Players who may miss the next gameweek, from FPL's latest news, most owned
// first. The API reads FPL for it and keeps the answer a few minutes.
export function useNews() {
  return useQuery({
    queryKey: ["news"],
    queryFn: () => unwrap(api.GET("/v1/news")),
    // Not worth a second request to FPL if the first failed.
    retry: false,
  });
}

// An entry covers every player in the game, about 700; one request holds them all.
export const ENTRY_LIMIT = 1000;

// One model's whole entry for a gameweek. The predictions page, the front
// page's top picks and the optimiser's photos share it through this key.
export function useEntry(gameweek: number | null | undefined, model: string | null | undefined) {
  return useQuery({
    queryKey: ["predictions", gameweek, model],
    enabled: !!gameweek && !!model,
    queryFn: () =>
      unwrap(
        api.GET("/v1/gameweeks/{gameweek}/predictions", {
          params: { path: { gameweek: gameweek! }, query: { model: model!, limit: ENTRY_LIMIT } },
        }),
      ),
  });
}

// {player id: FPL player code} from an entry, for photos where a response
// (the optimiser's picks) has ids but no codes. It is the entry the
// predictions page fetches, under the same key, so it is often cached.
export function usePhotoCodes(
  gameweek: number | null | undefined,
  model: string | null | undefined,
) {
  const entry = useEntry(gameweek, model);
  return useMemo(
    () => new Map((entry.data?.items ?? []).map((p) => [p.player_id, p.player_code])),
    [entry.data],
  );
}

// FPL's positions, in the order a team sheet lists them.
export const POSITIONS = ["GKP", "DEF", "MID", "FWD"] as const;

export const POSITION_NAMES: Record<string, string> = {
  GKP: "Goalkeepers",
  DEF: "Defenders",
  MID: "Midfielders",
  FWD: "Forwards",
};
