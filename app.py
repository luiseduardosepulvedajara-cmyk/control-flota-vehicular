"""Sistema de Control de Flota Vehicular - Streamlit + Supabase.

Perfiles: Guardia de Garita, Conductor Operativo y Administrador de Puesto de Mando.
Ejecutar desde la carpeta del proyecto:  streamlit run app.py
Credenciales en .streamlit/secrets.toml (no se suben a GitHub).
"""
from __future__ import annotations

import hmac
import io
import re
import uuid
from typing import Any

import pandas as pd
import streamlit as st
from PIL import Image
from supabase import Client, create_client

st.set_page_config(page_title="Control de Flota", page_icon="🚙", layout="wide")

# =====================================================================
# CONFIG
# =====================================================================
BUCKET_EVIDENCIAS = "evidencias-vehiculares"
TAMANO_MAXIMO_BYTES = 800 * 1024  # 800 KB, limite del bucket

ESTADO_OPERATIVO = "Operativo"
ESTADO_MANTENCION = "Requiere Mantención"
ESTADO_CRITICO = "Crítico"


@st.cache_resource
def obtener_cliente() -> Client:
    """Crea un unico cliente de Supabase compartido por toda la app."""
    try:
        url = st.secrets["SUPABASE_URL"]
        clave = (
            st.secrets.get("SUPABASE_ANON_KEY")
            or st.secrets.get("SUPABASE_PUBLISHABLE_KEY")
        )
    except (KeyError, FileNotFoundError) as error:
        raise RuntimeError(
            "Falta .streamlit/secrets.toml con SUPABASE_URL y una clave publica de Supabase."
        ) from error
    if not clave:
        raise RuntimeError(
            "Configura SUPABASE_ANON_KEY o SUPABASE_PUBLISHABLE_KEY en .streamlit/secrets.toml."
        )
    if str(clave).startswith("sb_secret_"):
        raise RuntimeError(
            "No uses una clave sb_secret_ en la aplicacion. Configura la clave publica del proyecto."
        )
    return create_client(url, clave)


# =====================================================================
# SERVICIOS
# =====================================================================
PATRON_PATENTE = re.compile(r"^[A-Z0-9]{2,4}-?[A-Z0-9]{2,4}$")
PATRON_RUT = re.compile(r"^\d{1,2}\.?\d{3}\.?\d{3}-[\dK]$")

FALLAS_CRITICAS = {
    "frenos": "FRENOS",
    "neumaticos_severo": "NEUMATICOS-PRESION-SEVERA",
    "direccion": "DIRECCION",
    "motor": "MOTOR",
}


# ---------------------------------------------------------------------
# Normalizacion y validacion de entradas
# ---------------------------------------------------------------------
def normalizar_patente(valor: str) -> str:
    """'abcd 12' / 'ABCD12' -> 'ABCD-12' (formato de la tabla vehiculos)."""
    limpio = re.sub(r"[^A-Z0-9]", "", (valor or "").upper())
    if len(limpio) == 6:
        return f"{limpio[:4]}-{limpio[4:]}"
    return limpio


def normalizar_rut(valor: str) -> str:
    """'18234567k' / '18.234.567-K' -> '18.234.567-K' (formato de la tabla conductores)."""
    limpio = re.sub(r"[^0-9K]", "", (valor or "").upper())
    if len(limpio) < 2:
        return limpio
    cuerpo, dv = limpio[:-1], limpio[-1]
    cuerpo_con_puntos = f"{int(cuerpo):,}".replace(",", ".") if cuerpo.isdigit() else cuerpo
    return f"{cuerpo_con_puntos}-{dv}"


def patente_valida(patente: str) -> bool:
    return bool(PATRON_PATENTE.match(patente)) and len(patente) <= 10


def rut_valido(rut: str) -> bool:
    return bool(PATRON_RUT.match(rut)) and len(rut) <= 12


# ---------------------------------------------------------------------
# Lecturas de tablas maestras
# ---------------------------------------------------------------------
def listar_vehiculos() -> list[dict[str, Any]]:
    return obtener_cliente().table("vehiculos").select("*").order("patente").execute().data or []


def listar_conductores() -> list[dict[str, Any]]:
    return obtener_cliente().table("conductores").select("*").order("nombre_completo").execute().data or []


def listar_autorizaciones() -> list[dict[str, Any]]:
    return obtener_cliente().table("autorizaciones").select("*").execute().data or []


def listar_reportes(patente: str | None = None, limite: int = 200) -> list[dict[str, Any]]:
    consulta = obtener_cliente().table("reportes_estado").select("*")
    if patente:
        consulta = consulta.eq("patente", patente)
    return consulta.order("fecha_reporte", desc=True).limit(limite).execute().data or []


def vehiculos_autorizados_para(rut: str) -> list[str]:
    datos = (
        obtener_cliente()
        .table("autorizaciones")
        .select("patente")
        .eq("rut_conductor", rut)
        .eq("vigente", True)
        .execute()
        .data
        or []
    )
    return sorted({fila["patente"] for fila in datos})


# ---------------------------------------------------------------------
# Modulo Guardia: validacion cruzada
# ---------------------------------------------------------------------
def validar_acceso(patente: str, rut: str) -> dict[str, Any]:
    """Valida simultaneamente vehiculo, conductor y autorizacion vigente.

    1) vehiculos: existe la patente y su estado operativo.
    2) conductores: existe el RUT y su estado es 'Activo'.
    3) autorizaciones: existe el par (patente, rut) con vigente = TRUE.
    Resultado: Autorizado / Denegado / Alerta de Estado.
    """
    cliente = obtener_cliente()

    vehiculo = cliente.table("vehiculos").select("*").eq("patente", patente).limit(1).execute().data
    conductor = cliente.table("conductores").select("*").eq("rut", rut).limit(1).execute().data
    autorizacion = (
        cliente.table("autorizaciones")
        .select("id, fecha_asignacion")
        .eq("patente", patente)
        .eq("rut_conductor", rut)
        .eq("vigente", True)
        .limit(1)
        .execute()
        .data
    )

    resultado: dict[str, Any] = {
        "patente": patente,
        "rut": rut,
        "vehiculo": vehiculo[0] if vehiculo else None,
        "conductor": conductor[0] if conductor else None,
    }

    if not vehiculo:
        return {**resultado, "resolucion": "Denegado", "detalle": "Patente no registrada en el catálogo de la flota."}
    if not conductor:
        return {**resultado, "resolucion": "Denegado", "detalle": "RUT no registrado como conductor habilitado."}
    if conductor[0].get("estado") != "Activo":
        return {**resultado, "resolucion": "Denegado", "detalle": f"Conductor en estado '{conductor[0].get('estado')}'."}
    if not autorizacion:
        return {**resultado, "resolucion": "Denegado", "detalle": "El conductor no tiene autorización vigente para este vehículo."}

    estado = vehiculo[0].get("estado_operativo", ESTADO_OPERATIVO)
    if estado != ESTADO_OPERATIVO:
        return {
            **resultado,
            "resolucion": "Alerta de Estado",
            "detalle": f"Autorización vigente, pero el vehículo figura como '{estado}'.",
        }
    return {**resultado, "resolucion": "Autorizado", "detalle": "Conductor habilitado y autorización vigente."}


def tabla_accesos_disponible() -> bool:
    try:
        obtener_cliente().table("registros_acceso").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def registrar_acceso(resultado: dict[str, Any], puesto: str, foto_url: str | None, usuario: str) -> None:
    obtener_cliente().table("registros_acceso").insert(
        {
            "patente": resultado["patente"],
            "rut_conductor": resultado["rut"],
            "puesto_control": puesto[:80],
            "resolucion": resultado["resolucion"],
            "detalle": resultado["detalle"],
            "foto_evidencia_url": foto_url,
            "registrado_por": usuario[:80],
        }
    ).execute()


def listar_accesos(limite: int = 100, patente: str | None = None) -> list[dict[str, Any]]:
    try:
        consulta = obtener_cliente().table("registros_acceso").select("*")
        if patente:
            consulta = consulta.eq("patente", patente)
        return consulta.order("fecha_registro", desc=True).limit(limite).execute().data or []
    except Exception:
        return []


# ---------------------------------------------------------------------
# Storage: evidencias fotograficas
# ---------------------------------------------------------------------
def _comprimir_imagen(contenido: bytes) -> bytes:
    """Reduce la imagen a JPEG hasta quedar bajo 800 KB."""
    imagen = Image.open(io.BytesIO(contenido)).convert("RGB")
    imagen.thumbnail((1600, 1600))
    for calidad in (85, 75, 65, 55, 45, 35):
        salida = io.BytesIO()
        imagen.save(salida, format="JPEG", quality=calidad, optimize=True)
        if salida.tell() <= TAMANO_MAXIMO_BYTES:
            return salida.getvalue()
        imagen.thumbnail((imagen.width * 3 // 4, imagen.height * 3 // 4))
    raise ValueError("No fue posible reducir la imagen bajo 800 KB.")


def subir_evidencia(archivo: Any, carpeta: str, patente: str) -> str:
    """Sube la foto al bucket y devuelve su URL publica."""
    contenido = _comprimir_imagen(archivo.getvalue())
    patente_segura = re.sub(r"[^A-Z0-9-]", "", patente.upper()) or "SIN-PATENTE"
    ruta = f"{carpeta}/{patente_segura}/{uuid.uuid4().hex}.jpg"
    almacen = obtener_cliente().storage.from_(BUCKET_EVIDENCIAS)
    almacen.upload(ruta, contenido, {"content-type": "image/jpeg", "upsert": "false"})
    return almacen.get_public_url(ruta).rstrip("?")


# ---------------------------------------------------------------------
# Modulo Conductor: check-in y actualizacion automatica de estado
# ---------------------------------------------------------------------
def evaluar_criticidad(presion_ok: bool, luces_ok: bool, fallas: list[str], combustible: int) -> tuple[str, list[str]]:
    """Regla de negocio que determina el nuevo estado del vehiculo.

    - Falla critica (frenos, presion severa, direccion, motor), baja presion
      o falla de luces -> 'Requiere Mantención' en la tabla vehiculos.
    - Sin novedades -> Operativo (no se modifica el estado en la tabla maestra).
    El estado 'Crítico' lo asigna manualmente el Puesto de Mando.
    """
    motivos: list[str] = [FALLAS_CRITICAS[f] for f in fallas if f in FALLAS_CRITICAS]
    if not presion_ok:
        motivos.append("PRESION-NEUMATICOS")
    if not luces_ok:
        motivos.append("LUCES")
    if motivos:
        return ESTADO_MANTENCION, motivos
    if combustible < 15:
        return ESTADO_OPERATIVO, ["COMBUSTIBLE-BAJO"]
    return ESTADO_OPERATIVO, []


def registrar_reporte(
    patente: str,
    rut: str,
    kilometraje: int,
    combustible: int,
    presion_ok: bool,
    luces_ok: bool,
    fallas: list[str],
    observaciones: str,
    foto_url: str | None,
) -> dict[str, Any]:
    cliente = obtener_cliente()
    vehiculo = cliente.table("vehiculos").select("kilometraje_actual, estado_operativo").eq("patente", patente).limit(1).execute().data
    if not vehiculo:
        raise ValueError("La patente no existe en la flota.")
    km_actual = int(vehiculo[0].get("kilometraje_actual") or 0)
    if kilometraje < km_actual:
        raise ValueError(f"El kilometraje ({kilometraje:,}) no puede ser menor al registrado ({km_actual:,}).")

    nuevo_estado, motivos = evaluar_criticidad(presion_ok, luces_ok, fallas, combustible)
    hay_critica = any(f in FALLAS_CRITICAS for f in fallas)
    if motivos and nuevo_estado != ESTADO_OPERATIVO:
        etiquetas = f"[{'CRÍTICO' if hay_critica else 'FALLA'}:{','.join(motivos)}] "
    elif motivos:
        etiquetas = f"[AVISO:{','.join(motivos)}] "
    else:
        etiquetas = ""

    cliente.table("reportes_estado").insert(
        {
            "patente": patente,
            "rut_conductor": rut,
            "kilometraje_salida": kilometraje,
            "nivel_combustible": combustible,
            "presion_neumaticos": presion_ok,
            "luces_operativas": luces_ok,
            "observaciones": (etiquetas + observaciones.strip())[:2000] or None,
            "foto_evidencia_url": foto_url,
        }
    ).execute()

    # Actualizacion automatica de la tabla maestra
    cambios: dict[str, Any] = {"kilometraje_actual": kilometraje}
    if nuevo_estado != ESTADO_OPERATIVO:
        cambios["estado_operativo"] = nuevo_estado
    cliente.table("vehiculos").update(cambios).eq("patente", patente).execute()

    return {"estado_anterior": vehiculo[0].get("estado_operativo"), "estado_nuevo": cambios.get("estado_operativo", vehiculo[0].get("estado_operativo")), "motivos": motivos}


def actualizar_estado_vehiculo(patente: str, estado: str) -> None:
    obtener_cliente().table("vehiculos").update({"estado_operativo": estado}).eq("patente", patente).execute()


# =====================================================================
# AUTENTICACION
# =====================================================================
PERFILES = {
    "Guardia de Garita": "guardia",
    "Conductor Operativo": "conductor",
    "Administrador Puesto de Mando": "administrador",
}


def _credenciales_validas(perfil: str, usuario: str, clave: str) -> bool:
    try:
        datos = st.secrets["usuarios"][perfil]
    except KeyError:
        return False
    # compare_digest evita ataques por tiempo de comparacion
    return hmac.compare_digest(usuario.strip(), str(datos["usuario"])) and hmac.compare_digest(
        clave, str(datos["clave"])
    )


def mostrar_login() -> None:
    st.title("Sistema de Control de Flota")
    st.caption("Acceso vehicular, check-in de conductores y panel de Puesto de Mando")

    with st.form("login"):
        etiqueta = st.selectbox("Perfil", list(PERFILES.keys()))
        usuario = st.text_input("Usuario", max_chars=40)
        clave = st.text_input("Contraseña", type="password", max_chars=60)
        ingresar = st.form_submit_button("Ingresar", type="primary", width="stretch")

    if ingresar:
        perfil = PERFILES[etiqueta]
        if _credenciales_validas(perfil, usuario, clave):
            st.session_state["autenticado"] = True
            st.session_state["perfil"] = perfil
            st.session_state["usuario"] = usuario.strip()
            st.session_state["nombre"] = st.secrets["usuarios"][perfil].get("nombre", etiqueta)
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos para el perfil seleccionado.")


def barra_lateral() -> None:
    with st.sidebar:
        st.markdown(f"**{st.session_state.get('nombre', '')}**")
        st.caption(f"Perfil: {st.session_state.get('perfil', '')}")
        if st.button("Cerrar sesión", width="stretch"):
            for clave in list(st.session_state.keys()):
                del st.session_state[clave]
            st.rerun()


# =====================================================================
# GUARDIA
# =====================================================================
ESTILO_RESOLUCION = {
    "Autorizado": (st.success, "✅"),
    "Denegado": (st.error, "⛔"),
    "Alerta de Estado": (st.warning, "⚠️"),
}


def _mostrar_resolucion(resultado: dict) -> None:
    funcion, icono = ESTILO_RESOLUCION[resultado["resolucion"]]
    funcion(f"### {icono} {resultado['resolucion'].upper()}\n\n{resultado['detalle']}")

    col1, col2 = st.columns(2)
    if v := resultado.get("vehiculo"):
        col1.markdown(
            f"**Vehículo:** {v['patente']}  \n{v['marca_modelo']} ({v['anio']})  \n"
            f"Tipo: {v['tipo_vehiculo']}  \nEstado: {v['estado_operativo']}"
        )
    if c := resultado.get("conductor"):
        col2.markdown(
            f"**Conductor:** {c['nombre_completo']}  \n{c['grado_o_cargo']}  \n"
            f"{c['unidad_destino']}  \nEstado: {c['estado']}"
        )


def vista_guardia() -> None:
    st.title("🛂 Control de acceso vehicular")
    st.caption("Ingrese o escanee la patente y el RUT del conductor")

    tabla_ok = tabla_accesos_disponible()
    if not tabla_ok:
        st.info(
            "La validación funciona, pero el registro histórico requiere ejecutar "
            "`database/02_registros_acceso.sql` en Supabase."
        )

    with st.form("control_acceso", clear_on_submit=False):
        patente_txt = st.text_input("Patente", placeholder="ABCD-12", max_chars=10)
        rut_txt = st.text_input("RUT del conductor", placeholder="18.234.567-K", max_chars=12)
        puesto = st.text_input("Puesto de control", value="Garita Principal", max_chars=80)
        foto = st.camera_input("Evidencia fotográfica (patente)")
        foto_archivo = st.file_uploader("…o adjuntar imagen", type=["jpg", "jpeg", "png"])
        validar = st.form_submit_button("Validar acceso", type="primary", width="stretch")

    if validar:
        patente = normalizar_patente(patente_txt)
        rut = normalizar_rut(rut_txt)
        if not patente_valida(patente):
            st.error("Formato de patente inválido. Ejemplo: ABCD-12")
            return
        if not rut_valido(rut):
            st.error("Formato de RUT inválido. Ejemplo: 18.234.567-K")
            return

        with st.spinner("Consultando Supabase…"):
            try:
                resultado = validar_acceso(patente, rut)
            except Exception as error:
                st.error(f"Error al consultar la base de datos: {error}")
                return

            foto_url = None
            evidencia = foto or foto_archivo
            if evidencia is not None:
                try:
                    foto_url = subir_evidencia(evidencia, "accesos", patente)
                except Exception as error:
                    st.warning(f"No se pudo subir la evidencia: {error}")

            if tabla_ok:
                try:
                    registrar_acceso(resultado, puesto.strip() or "Garita Principal", foto_url, st.session_state["usuario"])
                except Exception as error:
                    st.warning(f"No se pudo guardar el registro: {error}")

        st.session_state["ultimo_resultado"] = resultado

    if resultado := st.session_state.get("ultimo_resultado"):
        _mostrar_resolucion(resultado)

    if tabla_ok:
        st.divider()
        st.subheader("Últimos accesos registrados")
        accesos = listar_accesos(limite=10)
        if accesos:
            df = pd.DataFrame(accesos)[["fecha_registro", "patente", "rut_conductor", "puesto_control", "resolucion"]]
            df["fecha_registro"] = pd.to_datetime(df["fecha_registro"]).dt.tz_convert("America/Santiago").dt.strftime("%d-%m %H:%M")
            st.dataframe(df, hide_index=True, width="stretch")
        else:
            st.caption("Sin registros todavía.")


# =====================================================================
# CONDUCTOR
# =====================================================================
OPCIONES_FALLAS = {
    "frenos": "Falla en sistema de frenos",
    "neumaticos_severo": "Baja presión SEVERA de neumáticos",
    "direccion": "Falla en dirección",
    "motor": "Falla de motor / temperatura",
}


def _seleccionar_conductor() -> dict | None:
    conductores = listar_conductores()
    activos = [c for c in conductores if c.get("estado") == "Activo"]
    etiquetas = {f"{c['rut']} · {c['grado_o_cargo']} {c['nombre_completo']}": c for c in activos}
    elegido = st.selectbox("Identificación del conductor", ["— Seleccione —", *etiquetas.keys()])
    return etiquetas.get(elegido)


def vista_conductor() -> None:
    st.title("🧾 Check-in operativo del conductor")
    st.caption("Reporte de condiciones del vehículo antes de la salida")

    try:
        conductor = _seleccionar_conductor()
    except Exception as error:
        st.error(f"No se pudo cargar la lista de conductores: {error}")
        return
    if not conductor:
        return

    patentes = vehiculos_autorizados_para(conductor["rut"])
    if not patentes:
        st.warning("Este conductor no tiene vehículos con autorización vigente.")
        return

    vehiculos = {v["patente"]: v for v in listar_vehiculos()}
    patente = st.selectbox("Vehículo asignado", patentes)
    vehiculo = vehiculos.get(patente, {})
    st.caption(
        f"{vehiculo.get('marca_modelo', '')} · Estado actual: **{vehiculo.get('estado_operativo', '—')}** · "
        f"Km registrado: {int(vehiculo.get('kilometraje_actual') or 0):,}".replace(",", ".")
    )

    with st.form("check_in", clear_on_submit=True):
        kilometraje = st.number_input(
            "Kilometraje (odómetro)", min_value=0, max_value=2_000_000, step=1,
            value=int(vehiculo.get("kilometraje_actual") or 0),
        )
        combustible = st.slider("Nivel de combustible (%)", 0, 100, 75)
        col1, col2 = st.columns(2)
        presion_ok = col1.toggle("Presión de neumáticos OK", value=True)
        luces_ok = col2.toggle("Luces operativas", value=True)
        fallas = st.multiselect(
            "Novedades mecánicas críticas",
            options=list(OPCIONES_FALLAS.keys()),
            format_func=OPCIONES_FALLAS.get,
        )
        observaciones = st.text_area("Observaciones", max_chars=500)
        foto = st.camera_input("Foto del odómetro / novedad")
        foto_archivo = st.file_uploader("…o adjuntar imagen", type=["jpg", "jpeg", "png"])
        enviar = st.form_submit_button("Enviar reporte", type="primary", width="stretch")

    if not enviar:
        _historial_conductor(patente)
        return

    foto_url = None
    evidencia = foto or foto_archivo
    try:
        if evidencia is not None:
            foto_url = subir_evidencia(evidencia, "reportes", patente)
        resultado = registrar_reporte(
            patente=patente,
            rut=conductor["rut"],
            kilometraje=int(kilometraje),
            combustible=int(combustible),
            presion_ok=presion_ok,
            luces_ok=luces_ok,
            fallas=fallas,
            observaciones=observaciones,
            foto_url=foto_url,
        )
    except ValueError as error:
        st.error(str(error))
        return
    except Exception as error:
        st.error(f"No se pudo registrar el reporte: {error}")
        return

    if resultado["estado_nuevo"] != resultado["estado_anterior"]:
        st.error(
            f"⚠️ Reporte registrado. El vehículo {patente} cambió automáticamente de "
            f"'{resultado['estado_anterior']}' a '{resultado['estado_nuevo']}' "
            f"({', '.join(resultado['motivos'])})."
        )
    elif resultado["motivos"]:
        st.warning(f"Reporte registrado con observaciones: {', '.join(resultado['motivos'])}.")
    else:
        st.success("✅ Reporte registrado. Vehículo sin novedades.")
    _historial_conductor(patente)


def _historial_conductor(patente: str) -> None:
    st.divider()
    st.subheader(f"Últimos reportes de {patente}")
    reportes = listar_reportes(patente=patente, limite=5)
    if not reportes:
        st.caption("Sin reportes previos.")
        return
    df = pd.DataFrame(reportes)
    df["fecha_reporte"] = pd.to_datetime(df["fecha_reporte"]).dt.tz_convert("America/Santiago").dt.strftime("%d-%m-%Y %H:%M")
    st.dataframe(
        df[["fecha_reporte", "kilometraje_salida", "nivel_combustible", "presion_neumaticos", "luces_operativas", "observaciones"]],
        hide_index=True, width="stretch",
    )


# =====================================================================
# ADMINISTRADOR
# =====================================================================
ZONA = "America/Santiago"
COLORES_ESTADO = {
    ESTADO_OPERATIVO: ("🟢", "background-color:#d8f3dc;color:#1b4332"),
    ESTADO_MANTENCION: ("🟡", "background-color:#fff3bf;color:#7a5b00"),
    ESTADO_CRITICO: ("🔴", "background-color:#ffd6d6;color:#8a1c1c"),
}


def _a_hora_local(serie: pd.Series) -> pd.Series:
    return pd.to_datetime(serie, utc=True).dt.tz_convert(ZONA)


def _kpis(df_v: pd.DataFrame, df_r: pd.DataFrame, df_a: pd.DataFrame) -> None:
    total = len(df_v)
    operativos = int((df_v["estado_operativo"] == ESTADO_OPERATIVO).sum())
    inmovilizados = int(df_v["estado_operativo"].isin([ESTADO_MANTENCION, ESTADO_CRITICO]).sum())
    criticos = int((df_v["estado_operativo"] == ESTADO_CRITICO).sum())
    disponibilidad = round(operativos / total * 100, 1) if total else 0.0

    hoy = pd.Timestamp.now(tz=ZONA).normalize()
    accesos_hoy = int((_a_hora_local(df_a["fecha_registro"]) >= hoy).sum()) if not df_a.empty else 0
    denegados_hoy = (
        int(((_a_hora_local(df_a["fecha_registro"]) >= hoy) & (df_a["resolucion"] != "Autorizado")).sum())
        if not df_a.empty else 0
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Disponibilidad de flota", f"{disponibilidad}%", f"{operativos}/{total} operativos", delta_color="off")
    c2.metric("Unidades inmovilizadas", inmovilizados, f"{criticos} en estado crítico", delta_color="inverse" if criticos else "off")
    c3.metric("Accesos hoy", accesos_hoy, f"{denegados_hoy} denegados/alerta", delta_color="inverse" if denegados_hoy else "off")
    c4.metric("Reportes de conductores", len(df_r))


def _matriz_estado(df_v: pd.DataFrame) -> None:
    st.subheader("Matriz de estado por vehículo")
    tabla = df_v[["patente", "tipo_vehiculo", "marca_modelo", "anio", "kilometraje_actual", "estado_operativo"]].copy()
    tabla.insert(0, "", tabla["estado_operativo"].map(lambda e: COLORES_ESTADO.get(e, ("⚪", ""))[0]))
    estilo = tabla.style.apply(
        lambda fila: [COLORES_ESTADO.get(fila["estado_operativo"], ("", ""))[1]] * len(fila), axis=1
    )
    st.dataframe(estilo, hide_index=True, width="stretch")

    with st.expander("Cambiar estado de un vehículo manualmente"):
        col1, col2, col3 = st.columns([2, 2, 1])
        patente = col1.selectbox("Patente", tabla["patente"].tolist(), key="cambio_patente")
        estado = col2.selectbox("Nuevo estado", list(COLORES_ESTADO.keys()), key="cambio_estado")
        if col3.button("Aplicar", width="stretch"):
            actualizar_estado_vehiculo(patente, estado)
            st.success(f"{patente} → {estado}")
            st.rerun()


def _alertas(df_r: pd.DataFrame) -> None:
    st.subheader("Alertas por fallas mecánicas")
    if df_r.empty:
        st.caption("Sin reportes.")
        return
    obs = df_r["observaciones"].fillna("")
    mascara = (~df_r["presion_neumaticos"]) | (~df_r["luces_operativas"]) | obs.str.contains(r"\[(?:CRÍTICO|FALLA)", regex=True)
    fallas = df_r[mascara].copy()
    if fallas.empty:
        st.success("No hay fallas reportadas.")
        return
    fallas["fecha"] = _a_hora_local(fallas["fecha_reporte"]).dt.strftime("%d-%m-%Y %H:%M")
    st.dataframe(
        fallas[["fecha", "patente", "rut_conductor", "presion_neumaticos", "luces_operativas", "observaciones"]],
        hide_index=True, width="stretch",
    )


def _analitica(df_v: pd.DataFrame, df_a: pd.DataFrame, df_c: pd.DataFrame) -> None:
    st.subheader("Analítica de uso y consumo")
    col1, col2 = st.columns(2)

    with col1:
        agrupar = st.radio("Kilometraje acumulado por", ["Tipo de vehículo", "Patente"], horizontal=True)
        campo = "tipo_vehiculo" if agrupar == "Tipo de vehículo" else "patente"
        km = df_v.groupby(campo, as_index=False)["kilometraje_actual"].sum().sort_values("kilometraje_actual", ascending=False)
        st.bar_chart(km, x=campo, y="kilometraje_actual", horizontal=True, y_label="Kilómetros", x_label="")

    with col2:
        if df_a.empty:
            st.caption("Frecuencia de accesos")
            st.info("Aún no hay accesos registrados.")
            return
        criterio = st.radio("Accesos por", ["Puesto de control", "Unidad del conductor"], horizontal=True)
        if criterio == "Puesto de control":
            conteo = df_a.groupby(["puesto_control", "resolucion"]).size().reset_index(name="accesos")
            st.bar_chart(conteo, x="puesto_control", y="accesos", color="resolucion", x_label="")
        else:
            unidades = df_a.merge(df_c[["rut", "unidad_destino"]], left_on="rut_conductor", right_on="rut", how="left")
            unidades["unidad_destino"] = unidades["unidad_destino"].fillna("No registrado")
            conteo = unidades.groupby(["unidad_destino", "resolucion"]).size().reset_index(name="accesos")
            st.bar_chart(conteo, x="unidad_destino", y="accesos", color="resolucion", horizontal=True, x_label="")


def _feed_accesos(df_a: pd.DataFrame) -> None:
    st.subheader("Feed cronológico de accesos")
    if df_a.empty:
        st.info("Sin accesos registrados. Ejecute `database/02_registros_acceso.sql` si aún no lo hace.")
        return
    iconos = {"Autorizado": "✅", "Denegado": "⛔", "Alerta de Estado": "⚠️"}
    for _, fila in df_a.head(15).iterrows():
        with st.container(border=True):
            col_txt, col_img = st.columns([3, 1])
            hora = _a_hora_local(pd.Series([fila["fecha_registro"]])).iloc[0].strftime("%d-%m-%Y %H:%M")
            col_txt.markdown(
                f"{iconos.get(fila['resolucion'], '•')} **{fila['resolucion']}** · {fila['patente']} · "
                f"{fila['rut_conductor']}  \n{hora} · {fila['puesto_control']}  \n_{fila.get('detalle') or ''}_"
            )
            if fila.get("foto_evidencia_url"):
                col_img.image(fila["foto_evidencia_url"], width="stretch")
            else:
                col_img.caption("Sin foto")


def _auditoria(df_v: pd.DataFrame) -> None:
    st.subheader("Auditoría de incidencias por patente")
    patente = st.selectbox("Seleccione patente", df_v["patente"].tolist(), key="auditoria_patente")
    reportes = listar_reportes(patente=patente)
    accesos = listar_accesos(patente=patente, limite=50)

    tab_rep, tab_acc = st.tabs([f"Reportes de conductores ({len(reportes)})", f"Accesos ({len(accesos)})"])
    with tab_rep:
        if not reportes:
            st.caption("Sin reportes para esta patente.")
        for r in reportes:
            with st.container(border=True):
                col_txt, col_img = st.columns([3, 1])
                fecha = pd.to_datetime(r["fecha_reporte"]).tz_convert(ZONA).strftime("%d-%m-%Y %H:%M")
                col_txt.markdown(
                    f"**{fecha}** · Conductor {r['rut_conductor']}  \n"
                    f"Km: {r['kilometraje_salida']:,} · Combustible: {r.get('nivel_combustible') or '—'}%  \n"
                    f"Neumáticos: {'OK' if r['presion_neumaticos'] else '❌ Baja presión'} · "
                    f"Luces: {'OK' if r['luces_operativas'] else '❌ Falla'}  \n"
                    f"_{r.get('observaciones') or 'Sin observaciones'}_".replace(",", ".")
                )
                if r.get("foto_evidencia_url"):
                    col_img.image(r["foto_evidencia_url"], width="stretch")
                else:
                    col_img.caption("Sin foto")
    with tab_acc:
        if accesos:
            df = pd.DataFrame(accesos)
            df["fecha_registro"] = _a_hora_local(df["fecha_registro"]).dt.strftime("%d-%m-%Y %H:%M")
            st.dataframe(
                df[["fecha_registro", "rut_conductor", "puesto_control", "resolucion", "detalle", "foto_evidencia_url"]],
                hide_index=True, width="stretch",
                column_config={"foto_evidencia_url": st.column_config.ImageColumn("Evidencia")},
            )
        else:
            st.caption("Sin accesos para esta patente.")


@st.fragment(run_every="30s")
def _panel_tiempo_real() -> None:
    """Se recarga solo cada 30 s para reflejar los datos en tiempo real."""
    try:
        df_v = pd.DataFrame(listar_vehiculos())
        df_r = pd.DataFrame(listar_reportes())
        df_a = pd.DataFrame(listar_accesos(limite=500))
        df_c = pd.DataFrame(listar_conductores())
    except Exception as error:
        st.error(f"No se pudieron cargar los datos: {error}")
        return
    if df_v.empty:
        st.warning("La tabla vehiculos está vacía.")
        return

    st.caption(f"Actualizado: {pd.Timestamp.now(tz=ZONA).strftime('%H:%M:%S')} (refresco automático cada 30 s)")
    _kpis(df_v, df_r, df_a)
    st.divider()
    col_izq, col_der = st.columns([3, 2])
    with col_izq:
        _matriz_estado(df_v)
    with col_der:
        _alertas(df_r)
    st.divider()
    _analitica(df_v, df_a, df_c)


def vista_administrador() -> None:
    st.title("📊 Puesto de Mando · Panel BI")
    _panel_tiempo_real()
    st.divider()
    col1, col2 = st.columns(2)
    df_v = pd.DataFrame(listar_vehiculos())
    with col1:
        _feed_accesos(pd.DataFrame(listar_accesos(limite=15)))
    with col2:
        _auditoria(df_v)


# =====================================================================
# MAIN
# =====================================================================
def main() -> None:
    try:
        obtener_cliente()
    except Exception as error:
        st.error(f"No se pudo conectar con Supabase: {error}")
        st.stop()

    if not st.session_state.get("autenticado"):
        mostrar_login()
        return

    barra_lateral()

    # Navegacion condicional segun el perfil guardado en la sesion
    vistas = {
        "guardia": vista_guardia,
        "conductor": vista_conductor,
        "administrador": vista_administrador,
    }
    vistas[st.session_state["perfil"]]()


main()