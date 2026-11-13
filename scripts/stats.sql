-- Local overview-counter control for the verified n8n Insights schema.
CREATE TABLE IF NOT EXISTS school_stats_pause_windows (
    id BIGSERIAL PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS school_stats_one_open_pause
ON school_stats_pause_windows(workflow_id) WHERE ended_at IS NULL;
CREATE OR REPLACE FUNCTION school_filter_paused_insights() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM insights_metadata m
        JOIN school_stats_pause_windows p ON p.workflow_id = m."workflowId"
        WHERE m."metaId" = NEW."metaId"
          AND NEW."timestamp" >= p.started_at
          AND (p.ended_at IS NULL OR NEW."timestamp" < p.ended_at)
    ) THEN
        -- Preserve the inserted row/RETURNING contract used by TypeORM.
        -- All overview measures are SUM(value), so zero values do not count.
        NEW.value := 0;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS school_stats_pause_filter ON insights_raw;
CREATE TRIGGER school_stats_pause_filter BEFORE INSERT ON insights_raw
FOR EACH ROW EXECUTE FUNCTION school_filter_paused_insights();
