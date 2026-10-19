[🇬🇧 English](README.md) | **🇪🇸 Español**

# Monitor Docker para Home Assistant — fix de contenedores recreados (fork)

<div align="center">

**Un fork mantenido de [ualex73/monitor_docker](https://github.com/ualex73/monitor_docker)** con el arreglo de la recreación de contenedores:

**Watchtower / `docker compose up -d` / podman ya no rompen tus monitores.**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![upstream](https://img.shields.io/badge/fork%20of-ualex73%2Fmonitor__docker-blue.svg?style=for-the-badge)](https://github.com/ualex73/monitor_docker)
[![fixes](https://img.shields.io/badge/fixes-upstream%20%23188-green.svg?style=for-the-badge)](https://github.com/ualex73/monitor_docker/issues/188)

</div>

## El problema que arregla este fork (issue #183 de upstream → #188)

Cuando un contenedor se **recrea** — actualización de Watchtower, `docker compose up -d`, reinicio en podman, ... — la integración original seguía monitorizando la **ID antigua del contenedor** para siempre:

- 📉 Las entidades quedaban **atascadas/desconocidas** hasta reiniciar Home Assistant.
- 🔁 Spam en el log cada 60 segundos: `Container not available anymore (3a) ([404] No such container: ...) Retry in 60 seconds`.
- 🧟 Si se perdía el evento `destroy` de Docker (proxy de Docker, carreras), un evento `create` posterior se descartaba con `Container already monitored`, dejando un monitor zombi.

**Este fork se auto-repara.** Ante un 404 se re-adjunta al contenedor recreado por nombre (`Re-attached to recreated container`); si el contenedor realmente ya no existe, elimina el monitor limpiamente para que las entidades se vuelvan a crear con el siguiente evento `create`. Los deltas de estadísticas (CPU/red/memoria) se reinician al re-adjuntar, así que desaparecen los avisos `network tx/rx became negative` tras una actualización.

Verificado en producción: recrea un contenedor con Watchtower o compose y el monitor se recupera solo — sin reinicios, sin bucles de 404.

## Configuración por la interfaz (v2.0+)

Desde la v2.0.0 esta integración es **configurable desde la interfaz de Home Assistant** (config flow, issue upstream #173):

- Se añade desde *Configuración → Dispositivos y servicios → Añadir integración → Monitor Docker*.
- El asistente conecta con el host Docker, permite seleccionar contenedores y condiciones, y hay un diálogo de **Reconfigurar** disponible después desde la tarjeta de la integración.
- Una configuración existente en `configuration.yaml` se **importa automáticamente** a una entrada de configuración (después puedes quitarla del YAML; Home Assistant genera un aviso de reparación como recordatorio).
- Si se pierde la conexión con Docker, aparece un diálogo de **re-autenticación** en vez de un fallo silencioso.
- Interfaz **en español e inglés** (y más idiomas por añadir).

## Instalación (HACS)

Instalación como **repositorio personalizado**:

1. HACS → Integraciones → **⋮** → *Añadir repositorio personalizado*.
2. URL del repositorio: `https://github.com/ciltocruz/monitor_docker` — Categoría: **Integración**.
3. Instala **Monitor Docker (recreate fix)**.
4. Reinicia Home Assistant.

Instalación manual: copia `custom_components/monitor_docker/` (del zip de una [release](https://github.com/ciltocruz/monitor_docker/releases)) en tu directorio `custom_components` y reinicia Home Assistant.

## Sobre upstream

Este componente fue desarrollado por ualex73 para monitorizar un entorno Docker desde [Home-Assistant](https://www.home-assistant.io), inspirado en [Docker Monitor](https://github.com/Sanderhuisman/docker_monitor) de Sander Huisman (migrado de hilos a asyncio). Sobre ese trabajo, este fork mantiene el fix de recreación y la configuración por interfaz. El **README completo en inglés** (ejemplos de `docker run`/compose, soporte TLS, Raspberry Pi, opciones de configuración detalladas, preguntas frecuentes) está en [README.md](README.md).

## Créditos

- [ualex73](https://github.com/ualex73) — autor original de Monitor Docker.
- [dala318](https://github.com/dala318) — config flow y flujos de reconfigure/reauth/import (upstream #173, PRs #174–#183).
- [ak111pro](https://github.com/ak111pro) — finalización del config flow: compatibilidad con HA 2026.10, preservación de entity_ids heredados, filtro de contenedores seleccionados, dispatcher para contenedores tardíos, tests y CI.

## Licencia

[MIT](LICENSE.md)
