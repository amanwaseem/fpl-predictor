// Queries more than one page reads, defined once so their keys and options agree.
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";

// Every logged model: its gameweeks and role. It needs only the log.
export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: () => unwrap(api.GET("/v1/models")) });
}

// FPL's positions, in the order a team sheet lists them.
export const POSITIONS = ["GKP", "DEF", "MID", "FWD"] as const;
