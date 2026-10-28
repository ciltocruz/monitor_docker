[🇬🇧 English](README.md) | **🇪🇸 Español**

# Monitor Docker para Home Assistant — fork mantenido

<div align="center">

**Un fork mantenido de [ualex73/monitor_docker](https://github.com/ualex73/monitor_docker)** que mantiene vivo el original y añade lo que le faltaba:

**Configuración por UI · monitores auto-reparables · compatibilidad moderna con HA · bilingüe (ES/EN).**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![upstream](https://img.shields.io/badge/fork%20of-ualex73%2Fmonitor__docker-blue.svg?style=for-the-badge)](https://github.com/ualex73/monitor_docker)
[![release](https://img.shields.io/github/v/release/ciltocruz/monitor_docker.svg?style=for-the-badge)](https://github.com/ciltocruz/monitor_docker/releases)

</div>

## Qué añade este fork

- 🖥️ **Configuración 100% por interfaz** — añadir, reconfigurar y re-autenticar la integración desde *Configuración → Dispositivos y servicios*. Sin YAML (issue upstream #173).
- 🩹 **Monitores auto-reparables** — los contenedores recreados por Watchtower, `docker compose up -d` o podman se re-adjuntan solos; adiós a los bucles 404, los monitores zombi y los reinicios (issue upstream #188).
- 📦 **Migración de YAML sin dolor** — una configuración existente en `configuration.yaml` se importa sola a una entrada de configuración, conservando tus **entity IDs y nombres** para que dashboards y automatizaciones sigan funcionando.
- 🧩 **Agrupación por dispositivos** — un dispositivo por host Docker y uno por contenedor, en vez de una lista plana de entidades sueltas.
- 🎛️ **Switches y botones gestionables desde la UI** — unique IDs estables: renombrar, ocultar o deshabilitar desde la interfaz de HA (upstream #212).
- ⚡ **Refresco a demanda** — los eventos de arranque/parada/reinicio/salud actualizan las entidades al instante, no en el siguiente intervalo.
- 🐳 **Compatibilidad con Home Assistant 2026.10** — contenedores tardíos vía dispatcher, monitorización solo de los contenedores seleccionados, obtención de stats one-shot y fix de la carrera renombrado+create.
- 🇪🇸 **Interfaz en español** — el config flow traducido al completo; el inglés, intacto.
- ✅ **Tests y CI** — suite de pytest más workflows de validación hassfest/HACS.

## El fix de contenedores recreados en detalle (issue upstream #188)

Cuando un contenedor se **recrea** — actualización de Watchtower, `docker compose up -d`, reinicio en podman, ... — la integración original seguía monitorizando la **ID antigua del contenedor** para siempre:

- 📉 Las entidades quedaban **atascadas/desconocidas** hasta reiniciar Home Assistant.
- 🔁 Spam en el log cada 60 segundos: `Container not available anymore (3a) ([404] No such container: ...) Retry in 60 seconds`.
- 🧟 Si se perdía el evento `destroy` de Docker (proxy de Docker, carreras), un evento `create` posterior se descartaba con `Container already monitored`, dejando un monitor zombi.

**Este fork se auto-repara.** Ante un 404 se re-adjunta al contenedor recreado por nombre (`Re-attached to recreated container`); si el contenedor realmente ya no existe, elimina el monitor limpiamente para que las entidades se vuelvan a crear con el siguiente evento `create`. Los deltas de estadísticas (CPU/red/memoria) se reinician al re-adjuntar, así que desaparecen los avisos `network tx/rx became negative` tras una actualización.

### Retirada segura y diagnóstico

Tras dos ausencias consecutivas por nombre se retira el monitor: se cancela y espera su tarea, se liberan las suscripciones y se invocan una sola vez las retiradas de entidades. La retirada conserva la identidad del monitor; un 404 y un `destroy` concurrentes no pueden borrar un reemplazo. Los eventos duplicados son inocuos y los `destroy` retrasados con un ID antiguo se ignoran.

| Situación del contenedor | Nivel y frecuencia |
| --- | --- |
| Desaparición antes del enlace, recreación o reinicio de contadores de red | DEBUG; sin avisos duplicados. Los contadores nuevos establecen una base con velocidad cero |
| Estadísticas incompletas de CPU, memoria o red | DEBUG en la primera muestra; un WARNING en la tercera consecutiva por sección |
| Muestra vacía o fecha ausente/inválida | La misma política para la muestra completa; se borran los valores no disponibles |
| Excepción de consulta, incluida conexión o autenticación | ERROR en el primer fallo; DEBUG en los reintentos hasta una consulta correcta |

Una sección válida reinicia su racha de fallos. No se desactiva la recogida de red por muestras incompletas ni se aplican filtros globales. Un reemplazo detectado al recuperar un 404 se enlaza por nombre y reinicia sus bases de CPU/red/memoria y sus contadores de diagnóstico.

**Límites:** hace falta una API Docker accesible. Después de retirar un monitor, un contenedor futuro se descubre mediante el evento `create` o una recarga de la integración, no mediante una búsqueda periódica de nombres retirados. Sin el ID del actor no es posible distinguir un evento histórico de otro actual solo por nombre. Las retiradas de entidades se programan de forma asíncrona en Home Assistant: liberar las suscripciones no garantiza un instante exacto de desaparición del registro de entidades.

### Pruebas de regresión

```sh
python3 -m unittest discover -s tests/standalone -v
```

Estas pruebas aisladas compilan las clases y funciones reales de `helpers.py` mediante AST, con Docker y Home Assistant simulados. Controlan las carreras con barreras explícitas y comprueban registros, colas, tareas, suscripciones, nuevos IDs y niveles/cantidades de logs. No validan un Docker real ni las plataformas de entidades de Home Assistant. La suite completa requiere el entorno de CI:

```sh
python3.13 -m pip install -r requirements_test.txt
python3.13 -m pytest -q
```

Las pruebas no despliegan ni reinician producción.

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
