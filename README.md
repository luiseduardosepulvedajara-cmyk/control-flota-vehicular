# Control de acceso vehicular

## Objetivo
Sistema de control de acceso vehicular orientado a garita, jefatura y puesto de mando, con validacion de conductores y vehiculos, evidencia digital y panel operativo BI.

## Estructura

```text
nov12/
├── app.py
├── README.md
├── requirements.txt
├── .gitignore
├── .streamlit/
│   └── secrets.toml.example
├── docs/
│   └── INFORME_TECNICO.md
├── secrets/
│   └── .env.example
├── supabase/
│   ├── 01_esquema.sql
│   └── 02_registros_acceso_y_storage.sql
├── vehiculos.csv
├── conductores.csv
├── autorizaciones.csv
└── venv/
```

## Requisitos
- Python 3.10+
- Streamlit
- Supabase Python client
- pandas

## Instalacion
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Base de datos (ejecutar en orden en el SQL Editor de Supabase)
1. `supabase/01_esquema.sql` — tablas conductores, vehiculos, autorizaciones, reportes_estado + datos de prueba.
2. `supabase/02_registros_acceso_y_storage.sql` — tabla registros_acceso (bitácora de Guardia) y bucket evidencias-vehiculares (público, 800 KB máx).

## Variables de entorno
Editar `.streamlit/secrets.toml` (no se sube a GitHub) con:

```toml
SUPABASE_URL = "https://tu-proyecto.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "tu-clave-anon-publica"

[usuarios.guardia]
usuario = "..."
clave = "..."
nombre = "Guardia de Garita"
# ...idem para [usuarios.conductor] y [usuarios.administrador]
```

La app no usa Supabase Auth: la sesión de cada perfil se valida contra las credenciales
definidas en `[usuarios]` dentro de `secrets.toml`, y Streamlit guarda el perfil activo
en `st.session_state` para la navegación condicional entre vistas.

## Ejecucion
```bash
streamlit run app.py
```

## Roles en la aplicacion
- Guardia de Garita: valida patente + RUT y registra el acceso con evidencia.
- Conductor Operativo: hace check-in del vehículo (km, combustible, novedades).
- Administrador de Puesto de Mando: panel BI con KPIs, feed de accesos y auditoría.

## Bucket y limitaciones
- Bucket: `evidencias-vehiculares`
- Limite de imagen: 800 KB

## Base de datos relevante
- conductores
- vehiculos
- autorizaciones
- reportes_estado

## Entregable
Este repositorio deja preparada la parte funcional, SQL y documentacion para presentar la actividad con una arquitectura modular y ejecutable localmente.
