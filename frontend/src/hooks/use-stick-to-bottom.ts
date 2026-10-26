"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

const THRESHOLD_PX = 72;

/**
 * Keeps a scroll container pinned to the bottom while content grows, unless
 * the user has scrolled up. Scrolling back near the bottom re-enables it.
 * `paused` (e.g. an empty-state screen) keeps the container at the top instead.
 */
export function useStickToBottom<T extends HTMLElement>(contentKey: unknown, paused = false) {
  const ref = useRef<T | null>(null);
  const stuck = useRef(true);
  const pausedRef = useRef(paused);
  const [atBottom, setAtBottom] = useState(true);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < THRESHOLD_PX;
    stuck.current = near;
    setAtBottom(near);
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const el = ref.current;
    if (!el) return;
    stuck.current = true;
    setAtBottom(true);
    const reduce = behavior === "smooth" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({ top: el.scrollHeight, behavior: reduce ? "auto" : behavior });
  }, []);

  useLayoutEffect(() => {
    pausedRef.current = paused;
    const el = ref.current;
    if (!el) return;
    if (paused) el.scrollTop = 0;
    else if (stuck.current) el.scrollTop = el.scrollHeight;
  }, [contentKey, paused]);

  // Content can also grow without a React update (fonts, code blocks wrapping).
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (stuck.current && !pausedRef.current) el.scrollTop = el.scrollHeight;
    });
    for (const child of Array.from(el.children)) ro.observe(child);
    return () => ro.disconnect();
  }, []);

  return { ref, onScroll, atBottom, scrollToBottom };
}
