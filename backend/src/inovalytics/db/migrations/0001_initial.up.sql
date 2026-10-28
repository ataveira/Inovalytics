-- 0001: esquema base (projeto, dataset, classe, imagem, anotação de caixa delimitadora).
-- Migração imutável depois de mesclada (o checksum falha); mudanças entram em 0002_*.

CREATE TABLE project (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT project_name_key UNIQUE (name)
);

CREATE TABLE dataset (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id bigint NOT NULL REFERENCES project (id) ON DELETE CASCADE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT dataset_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT dataset_project_name_key UNIQUE (project_id, name),
    CONSTRAINT dataset_id_project_key UNIQUE (id, project_id)
);

CREATE TABLE label_class (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id bigint NOT NULL REFERENCES project (id) ON DELETE CASCADE,
    name text NOT NULL,
    position integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT label_class_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT label_class_position_nonneg CHECK (position >= 0),
    CONSTRAINT label_class_project_name_key UNIQUE (project_id, name),
    CONSTRAINT label_class_project_position_key UNIQUE (project_id, position),
    CONSTRAINT label_class_id_project_key UNIQUE (id, project_id)
);

CREATE TABLE image (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id bigint NOT NULL,
    project_id bigint NOT NULL,
    object_key text NOT NULL,
    file_name text NOT NULL,
    sha256 text NOT NULL,
    size_bytes bigint NOT NULL,
    width_px integer NOT NULL,
    height_px integer NOT NULL,
    split text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT image_dataset_fk FOREIGN KEY (dataset_id, project_id)
        REFERENCES dataset (id, project_id) ON DELETE CASCADE,
    CONSTRAINT image_sha256_format CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT image_size_positive CHECK (size_bytes > 0),
    CONSTRAINT image_width_positive CHECK (width_px > 0),
    CONSTRAINT image_height_positive CHECK (height_px > 0),
    CONSTRAINT image_split_valid CHECK (split IN ('train', 'val', 'test')),
    CONSTRAINT image_dataset_sha256_key UNIQUE (dataset_id, sha256),
    CONSTRAINT image_object_key_key UNIQUE (object_key),
    CONSTRAINT image_id_project_key UNIQUE (id, project_id)
);

CREATE TABLE annotation (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    image_id bigint NOT NULL,
    project_id bigint NOT NULL,
    class_id bigint NOT NULL,
    x_center double precision NOT NULL,
    y_center double precision NOT NULL,
    width double precision NOT NULL,
    height double precision NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT annotation_image_fk FOREIGN KEY (image_id, project_id)
        REFERENCES image (id, project_id) ON DELETE CASCADE,
    CONSTRAINT annotation_class_fk FOREIGN KEY (class_id, project_id)
        REFERENCES label_class (id, project_id) ON DELETE NO ACTION,
    CONSTRAINT annotation_x_center_range CHECK (x_center >= 0 AND x_center <= 1),
    CONSTRAINT annotation_y_center_range CHECK (y_center >= 0 AND y_center <= 1),
    CONSTRAINT annotation_width_range CHECK (width > 0 AND width <= 1),
    CONSTRAINT annotation_height_range CHECK (height > 0 AND height <= 1)
);

CREATE INDEX annotation_image_id_idx ON annotation (image_id);
CREATE INDEX annotation_class_id_idx ON annotation (class_id);
