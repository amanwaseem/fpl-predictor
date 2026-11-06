import { useEffect } from "react";
import { SITE_NAME } from "./brand";

// The tab's title: "<page> · <site>", or the site alone.
export function useTitle(page: string | null) {
  useEffect(() => {
    document.title = page ? `${page} · ${SITE_NAME}` : SITE_NAME;
  }, [page]);
}
