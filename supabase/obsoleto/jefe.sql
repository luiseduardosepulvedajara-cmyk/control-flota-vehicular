create table if not exists public.perfiles_jefe (
    usuario_id uuid primary key references auth.users (id) on delete cascade,
    nombre text not null,
    activo boolean not null default true,
    created_at timestamptz not null default now()
);

alter table public.perfiles_jefe enable row level security;
grant select on public.perfiles_jefe to authenticated;

alter table public.bitacora_accesos
    add column if not exists evaluacion_jefe text,
    add column if not exists observacion_jefe text,
    add column if not exists evaluado_por uuid references auth.users(id),
    add column if not exists evaluado_at timestamptz;

alter table public.bitacora_accesos
    drop constraint if exists bitacora_accesos_evaluacion_jefe_check;

alter table public.bitacora_accesos
    add constraint bitacora_accesos_evaluacion_jefe_check
    check (
        evaluacion_jefe is null
        or evaluacion_jefe in ('Confirmar acceso', 'Solicitar revision', 'Rechazar control')
    );

create index if not exists bitacora_accesos_evaluado_at_idx
    on public.bitacora_accesos (evaluado_at desc);

drop policy if exists "jefe lee su perfil activo" on public.perfiles_jefe;
create policy "jefe lee su perfil activo"
    on public.perfiles_jefe for select to authenticated
    using (usuario_id = (select auth.uid()) and activo);

drop policy if exists "jefe lee toda la bitacora" on public.bitacora_accesos;
create policy "jefe lee toda la bitacora"
    on public.bitacora_accesos for select to authenticated
    using (
        exists (
            select 1 from public.perfiles_jefe pj
            where pj.usuario_id = (select auth.uid()) and pj.activo
        )
    );

create or replace function public.evaluar_control_jefe(
    p_bitacora_id bigint,
    p_decision text,
    p_observacion text
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, auth
as $$
declare
    v_jefe_id uuid := auth.uid();
begin
    if v_jefe_id is null or not exists (
        select 1 from public.perfiles_jefe pj
        where pj.usuario_id = v_jefe_id and pj.activo
    ) then
        raise exception 'Se requiere una cuenta de Jefe activa.';
    end if;

    if p_decision is null or trim(p_decision) = '' then
        raise exception 'La decision del jefe es obligatoria.';
    end if;

    update public.bitacora_accesos
    set evaluacion_jefe = trim(p_decision),
        observacion_jefe = coalesce(trim(p_observacion), 'Sin comentarios.'),
        evaluado_por = v_jefe_id,
        evaluado_at = now()
    where id = p_bitacora_id;
end;
$$;

revoke all on function public.evaluar_control_jefe(bigint, text, text) from public, anon;
grant execute on function public.evaluar_control_jefe(bigint, text, text) to authenticated;
