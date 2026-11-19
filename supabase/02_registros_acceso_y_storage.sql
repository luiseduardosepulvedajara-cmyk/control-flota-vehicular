-- =====================================================================
-- Complemento al script base (01_esquema.sql / Sesion12-SQL.txt).
-- Agrega lo que app.py realmente usa: bitacora de accesos de Guardia
-- y el bucket de evidencias fotograficas, SIN Supabase Auth (la app
-- gestiona el login propio con .streamlit/secrets.toml).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Tabla de accesos registrados por Guardia (usada en vista_guardia y
-- en el panel BI: feed, KPIs, analitica y auditoria por patente).
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS registros_acceso CASCADE;

CREATE TABLE registros_acceso (
    id SERIAL PRIMARY KEY,
    fecha_registro TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    patente VARCHAR(10) REFERENCES vehiculos(patente) ON UPDATE CASCADE,
    rut_conductor VARCHAR(12) REFERENCES conductores(rut) ON UPDATE CASCADE,
    puesto_control VARCHAR(80) NOT NULL,
    resolucion VARCHAR(20) NOT NULL CHECK (resolucion IN ('Autorizado', 'Denegado', 'Alerta de Estado')),
    detalle TEXT,
    foto_evidencia_url TEXT,
    registrado_por VARCHAR(80) NOT NULL
);

CREATE INDEX idx_registros_acceso_fecha ON registros_acceso (fecha_registro DESC);
CREATE INDEX idx_registros_acceso_patente ON registros_acceso (patente);

-- Supabase activa RLS por defecto en tablas nuevas; la desactivamos porque
-- la app usa la clave publica sin Supabase Auth (igual que el resto de tablas).
ALTER TABLE registros_acceso DISABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------
-- Bucket de evidencias fotograficas: publico (asi get_public_url()
-- sirve las imagenes directo en st.image), limite 800 KB, solo imagenes.
-- ---------------------------------------------------------------------
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('evidencias-vehiculares', 'evidencias-vehiculares', TRUE, 819200, ARRAY['image/jpeg', 'image/png'])
ON CONFLICT (id) DO UPDATE
SET public = TRUE,
    file_size_limit = 819200,
    allowed_mime_types = ARRAY['image/jpeg', 'image/png'];

-- La app usa la clave publica (anon/publishable), sin sesion de Supabase
-- Auth, para subir y leer evidencias. Se habilitan politicas abiertas
-- sobre ese bucket especifico (no afectan a otros buckets).
DROP POLICY IF EXISTS "evidencias lectura publica" ON storage.objects;
CREATE POLICY "evidencias lectura publica"
    ON storage.objects FOR SELECT
    USING (bucket_id = 'evidencias-vehiculares');

DROP POLICY IF EXISTS "evidencias carga publica" ON storage.objects;
CREATE POLICY "evidencias carga publica"
    ON storage.objects FOR INSERT
    WITH CHECK (bucket_id = 'evidencias-vehiculares');
