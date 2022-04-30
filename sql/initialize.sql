CREATE TYPE artifact_type AS ENUM (
    'tool',
    'package'
);

CREATE TABLE artifact_history (
    id integer serial PRIMARY KEY,
    project_name text NOT NULL,
    build_date timestamptz NOT NULL,
    artifact_type artifact_type NOT NULL,
    artifact_name text NOT NULL,
    artifact_version text NOT NULL,
    result_hash bytea NOT NULL
);
