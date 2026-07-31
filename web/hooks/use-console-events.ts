"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { ConsoleEvent } from "@/lib/events";
import { CONSOLE_EVENT_TYPES, parseSsePayload } from "@/lib/events";
import { evaluationEventFromReport } from "@/lib/evaluation";
import { MOCK_EVENT_INTERVAL_MS, PHASE_TWO_EVENTS } from "@/lib/mock-events";

export type StreamStatus = "connecting" | "live" | "replay" | "paused";

type ConsoleStream = {
  events: ConsoleEvent[];
  status: StreamStatus;
  replay: () => void;
  pause: () => void;
};

const endpoint = process.env.NEXT_PUBLIC_POSTMORTEM_EVENTS_URL;
const evaluationEndpoint =
  process.env.NEXT_PUBLIC_POSTMORTEM_EVALUATION_URL ?? "/phase2-evaluation.json";

export function useConsoleEvents(): ConsoleStream {
  const [events, setEvents] = useState<ConsoleEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const timers = useRef<number[]>([]);
  const source = useRef<EventSource | null>(null);
  const evaluationAbort = useRef<AbortController | null>(null);

  const clearTransport = useCallback(() => {
    timers.current.forEach(window.clearTimeout);
    timers.current = [];
    source.current?.close();
    source.current = null;
    evaluationAbort.current?.abort();
    evaluationAbort.current = null;
  }, []);

  const replay = useCallback(() => {
    clearTransport();
    setEvents([]);
    setStatus("replay");

    PHASE_TWO_EVENTS.forEach((event, index) => {
      const timer = window.setTimeout(() => {
        setEvents((current) => [...current, event]);
      }, index * MOCK_EVENT_INTERVAL_MS);
      timers.current.push(timer);
    });
  }, [clearTransport]);

  const pause = useCallback(() => {
    clearTransport();
    setStatus("paused");
  }, [clearTransport]);

  useEffect(() => {
    if (!endpoint) {
      const replayTimer = window.setTimeout(replay, 0);
      timers.current.push(replayTimer);
      return clearTransport;
    }

    const eventSource = new EventSource(endpoint);
    source.current = eventSource;

    eventSource.onopen = () => {
      setStatus("live");
      const controller = new AbortController();
      evaluationAbort.current = controller;
      void fetch(evaluationEndpoint, { signal: controller.signal })
        .then((response) => {
          if (!response.ok) throw new Error(`evaluation HTTP ${response.status}`);
          return response.json() as Promise<unknown>;
        })
        .then((report) => {
          // Bail if the effect was torn down (unmount / reconnect) mid-flight so
          // we never call setState on an unmounted component.
          if (controller.signal.aborted) return;
          const evaluation = evaluationEventFromReport(report);
          if (evaluation) {
            setEvents((current) =>
              current.some(({ id }) => id === evaluation.id)
                ? current
                : [...current, evaluation],
            );
          }
        })
        .catch(() => {
          // The live incident stream remains useful when the optional scorecard
          // is absent or the fetch was aborted on teardown.
        });
    };
    const receive = (message: MessageEvent<string>) => {
      const event = parseSsePayload(message.data);
      if (event) {
        setEvents((current) =>
          current.some(({ id }) => id === event.id) ? current : [...current, event],
        );
      }
    };
    eventSource.onmessage = receive;
    CONSOLE_EVENT_TYPES.forEach((type) => eventSource.addEventListener(type, receive));
    eventSource.onerror = () => {
      eventSource.close();
      replay();
    };

    return clearTransport;
  }, [clearTransport, replay]);

  return { events, status, replay, pause };
}
