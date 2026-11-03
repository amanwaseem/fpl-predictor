// Queries more than one page reads, defined once so their keys and options agree.
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";

// Every logged model: its gameweeks and role. It needs only the log.
export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: () => unwrap(api.GET("/v1/models")) });
}

export function useStatus() {
  return useQuery({ queryKey: ["status"], queryFn: () => unwrap(api.GET("/v1/status")) });
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
// (the optimiser's picks) has ids but no codes.
export function usePhotoCodes(gameweek: number | null | undefined, model: string | null | undefined) {
  const entry = useEntry(gameweek, model);
  return new Map((entry.data?.items ?? []).map((p) => [p.player_id, p.player_code]));
}

// FPL's positions, in the order a team sheet lists them.
export const POSITIONS = ["GKP", "DEF", "MID", "FWD"] as const;

export const POSITION_NAMES: Record<string, string> = {
  GKP: "Goalkeepers",
  DEF: "Defenders",
  MID: "Midfielders",
  FWD: "Forwards",
};
