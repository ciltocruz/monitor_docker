# Container lifecycle and diagnostics
Objective: identity-safe retirement and actionable diagnostics. Branch: fix/container-lifecycle (commit, push, PR and new version authorized).
Verification: deterministic stdlib AST tests; HA suite when available. Delivery: single-pr; authorized merge after checks and v2.1.2 release; no HA deployment.

## Specs
S1. "Cuando un contenedor desaparece:

 - Su monitor debe retirarse y liberar tareas y suscripciones.
 - No debe quedar consultando un ID inexistente ni generando 404 repetidamente hasta reiniciar Home
   Assistant.
 - No debe necesitar un reinicio para recuperarse.
 - Si aparece un reemplazo con el mismo nombre y otro ID, debe monitorizarse correctamente.
 - La retirada debe ser segura aunque lleguen simultáneamente un 404, un evento destroy y la creación del
   reemplazo.
 - Una retirada antigua nunca debe eliminar el monitor nuevo del mismo nombre."
S2. "No basta con capturar el KeyError: hay que garantizar la limpieza de las colas, la identidad del monitor
 retirado y la incorporación del reemplazo."
S3. "Revisa su tratamiento:

 - Una desaparición o recreación normal no debe generar WARNING/ERROR duplicados; si aporta información
   de diagnóstico, usa DEBUG.
 - Una muestra estadística incompleta durante arranque o parada no debe presentarse automáticamente como
   una avería.
 - Un reinicio normal de contadores de red no debe tratarse como error.
 - Los fallos reales de conexión, autenticación o recogida persistente de estadísticas deben seguir
   siendo visibles, sin inundar el log.
 - No resuelvas esto con filtros globales ni ocultando todas las excepciones."
S4. "Reproduce primero la carrera con una prueba determinista, sin esperas temporales frágiles. Cubre
 desaparición definitiva, reemplazo con mismo nombre, desaparición antes del primer enlace, eventos
 duplicados y retirada concurrente.

 Comprueba registros, colas, tareas, suscripciones, conexión al nuevo ID, ausencia de consultas
 posteriores al antiguo y cantidad/nivel exactos de logs.

 Entrega una corrección sencilla con pruebas de regresión y documentación actualizada. Explica qué
 garantiza y qué no. No despliegues ni reinicies Home Assistant sin mi autorización."

S5. "cuando termines, lo subes todo al repo, haces PR y haces nueva versión, ya mañana actualizo yo manualmente en home assistant."

## Tasks
T1 | S1,S2,S4 | done | inline: identity/liveness revalidated after awaits; GREEN 22 standalone tests | commit: 7f4820230bbfd44419510561cac1256dd1ab2047
T2 | S3,S4 | done | inline: same helper file; no safe independent writer surface | commit: 7f4820230bbfd44419510561cac1256dd1ab2047
T3 | S1-S4 | done | independent verifier passed including focused same/new-ID recheck; EN/ES docs; native reviewDue=false | commit: 7f4820230bbfd44419510561cac1256dd1ab2047
T4 | S5 | in_progress | SSH push succeeded; native review approved/acknowledged; fix stale config-flow tests and HACS repository settings, then PR/CI/merge/release | work-unit: 7f4820230bbfd44419510561cac1256dd1ab2047; evidence: b06be50

## Log
L13. Focused independent recheck passed: same-ID and new-ID Event-barrier probes with real polling/info; exactly two old queries, one removal, old task done/subscribers empty, live replacement, zero WARNING/ERROR. All 22 standalone tests pass. Native assessment after staging: native medium, high verification by concurrency escalation; reviewDue=false (under_budget), so no START. Existing full HA suite will run in CI. Target has no branch protection; all three workflows must pass before merge. No approved-issue/type-label policy exists in target (issues disabled, no template; standard bug label available).
L12. Independent verifier reported one blocker: same-ID lookup/retirement race. Deterministic regression RED exit 1; revalidated registry identity, retirement, task identity and task liveness after awaits. A follow-up assertion initially counted update callbacks as removals; corrected to count remove=True only. GREEN 22 tests, compileall and diff check exit 0. T1 complete; focused recheck pending. No other verifier product blockers; HA suite known unavailable locally.
L1. Original request (verbatim):
Necesito corregir la gestión de contenedores que desaparecen o se recrean y eliminar los falsos errores
 asociados. No quiero silenciar indiscriminadamente la integración: quiero arreglar el comportamiento y
 que los logs distingan transiciones normales de fallos reales.

 Contexto

 En Home Assistant tengo instalado mi fork ciltocruz/monitor_docker, versión 2.1.1.

 Se ha verificado el código instalado mediante pruebas aisladas que ejecutan sus clases y métodos reales
 extraídos por AST, con Docker y Home Assistant simulados. No se modificó producción.

 SHA-256 del helpers.py examinado:

 ```text
   0b9bbfb4935083702d4fc694268533ba7fa340c8b94e28250ff55e2503a1337c
 ```

 Contrasta estos hallazgos con el código actual del repositorio; las líneas pueden haber cambiado.

 Comportamiento requerido

 Cuando un contenedor desaparece:

 - Su monitor debe retirarse y liberar tareas y suscripciones.
 - No debe quedar consultando un ID inexistente ni generando 404 repetidamente hasta reiniciar Home
   Assistant.
 - No debe necesitar un reinicio para recuperarse.
 - Si aparece un reemplazo con el mismo nombre y otro ID, debe monitorizarse correctamente.
 - La retirada debe ser segura aunque lleguen simultáneamente un 404, un evento destroy y la creación del
   reemplazo.
 - Una retirada antigua nunca debe eliminar el monitor nuevo del mismo nombre.

 Fallo reproducido

 El caso normal funciona: tras dos ausencias consecutivas por nombre, desaparecen el monitor y sus
 suscripciones, se cancela su tarea y los refrescos posteriores no hacen llamadas Docker adicionales.

 Pero existe una carrera entre la retirada automática y destroy:

 1. Ambas retiradas superan la comprobación de existencia del monitor.
 2. Ambas alcanzan el await asyncio.sleep(0.1) de limpieza.
 3. La primera elimina _containers[cname].
 4. La segunda intenta eliminarlo otra vez y produce un KeyError.
 5. Se registra:

 ```text
   ERROR [test]: container_create_destroy ('svc')
 ```

 6. Queda _event_destroy = {'svc': 0}.
 7. El siguiente create queda pendiente y no conecta el reemplazo.

 Estado reproducido:

 ```text
   registry = {}
   monitor_done = True
   event_loop_alive = True
   pending_create = {'svc': 0}
   pending_destroy = {'svc': 0}
   new_attach_calls = 0
 ```

 Referencias del código examinado:

 - helpers.py:898–929: retirada y espera de limpieza.
 - helpers.py:809–839: procesamiento y limpieza de eventos.
 - helpers.py:619–689: incorporación de eventos pendientes.
 - helpers.py:1354–1409: recuperación por nombre.
 - helpers.py:1942–1977: cancelación y retirada de suscripciones.

 No basta con capturar el KeyError: hay que garantizar la limpieza de las colas, la identidad del monitor
 retirado y la incorporación del reemplazo.

 Ruido que también hay que corregir

 Un contenedor temporal que desaparece antes de iniciar su monitorización genera dos avisos por el mismo
 incidente:

 ```text
   Container not available anymore (2a) (...)
   Problem during start of monitoring
 ```

 En cinco informes esto produjo 118 firmas distintas y 120 apariciones, principalmente por nombres
 temporales diferentes.

 También aparecen como ERROR muestras estadísticas transitorias incompletas:

 ```text
   Cannot determine CPU usage for container ('system_cpu_usage')
   Raw 'cpu_stats' ...
   Cannot determine memory usage for container ('usage')
   Raw 'memory_stats' {}
   Can not determine network usage for container ('networks')
   No 'networks' found in raw packet
 ```

 Revisa su tratamiento:

 - Una desaparición o recreación normal no debe generar WARNING/ERROR duplicados; si aporta información
   de diagnóstico, usa DEBUG.
 - Una muestra estadística incompleta durante arranque o parada no debe presentarse automáticamente como
   una avería.
 - Un reinicio normal de contadores de red no debe tratarse como error.
 - Los fallos reales de conexión, autenticación o recogida persistente de estadísticas deben seguir
   siendo visibles, sin inundar el log.
 - No resuelvas esto con filtros globales ni ocultando todas las excepciones.

 Pruebas y entrega

 Reproduce primero la carrera con una prueba determinista, sin esperas temporales frágiles. Cubre
 desaparición definitiva, reemplazo con mismo nombre, desaparición antes del primer enlace, eventos
 duplicados y retirada concurrente.

 Comprueba registros, colas, tareas, suscripciones, conexión al nuevo ID, ausencia de consultas
 posteriores al antiguo y cantidad/nivel exactos de logs.

 Entrega una corrección sencilla con pruebas de regresión y documentación actualizada. Explica qué
 garantiza y qué no. No despliegues ni reinicies Home Assistant sin mi autorización.

L2. Repository helper hash exactly matches request. Baseline 19cfe65; clean master.
L3. Explorer mapped identity loss across awaits and queue state used as worker ownership.
L4. Python 3.12.3 available, but pytest/aiodocker unavailable and Python 3.13 absent. Use standalone stdlib AST regression harness against real classes; report HA suite unavailable. No dependency installation or production action.

L5. T1: observed RED (5 failures) on unchanged classes, then GREEN (12 standalone regressions). Explicit Event barriers cover old retirement, concurrent destroy, attach/publication and replacement; queue and callback checks pass. No commit: no user authorization.

L6. T2: diagnostic RED exited 1 (4 failures, 1 ZeroDivisionError); GREEN all 19 standalone tests exited 0. Threshold: two transient samples allowed, WARNING once on third incomplete sample per section; success resets. Real polling failures: ERROR once per streak then DEBUG. Network resets/invalid elapsed establish new baseline. No global filters.

L7. User: "sigue"
L8. User: "cuando termines, lo subes todo al repo, haces PR y haces nueva versión, ya mañana actualizo yo manualmente en home assistant."
L9. Added T4 delivery. Verified origin github.com/ciltocruz/monitor_docker, default master; latest release v2.1.1 has no assets. Issues disabled, no PR template. HA pytest attempt exited 1: No module named pytest. Native assess unassessable until untracked scope declared; concurrency requires independent verifier regardless.
L10. Self-review additional RED (initial attachment KeyError and inherited network disable) corrected; GREEN 21 tests. Reopened T1/T2 during correction, now complete.
L11. User selected: "Una sola PR — menos recomendable por tamaño" and "Sí, fusionar y publicar v2.1.2". Delivery strategy single-pr, no chain. Merge and release authorized only after satisfactory checks/reviews; no HA deployment. Manifest patch version updated.

L14. Work-unit commit 7f4820230bbfd44419510561cac1256dd1ab2047 created on fix/container-lifecycle. Committed assessment against actual baseline 19cfe658aa79ecd0ec2d12781ba60f7d4a0134be: native medium, verification high by concurrency escalation, reviewDue=false/under_budget; independent verifier already passed. An initial assess used an incorrect abbreviated base and failed read-only; local Git identity was checked and the actual full baseline used, with no repository mutation from the failed assess.
L15. Authorized push `git push -u origin fix/container-lifecycle` failed exit 128 / HTTP 403: "Permission to ciltocruz/monitor_docker.git denied to ciltocruz." No retry, alternate credentials or permission changes attempted. T4 blocked on a GitHub credential authorized to write repository contents. PR, remote CI, merge and release not performed; production untouched.

L16. User: "ya" after GitHub credential setup instructions. Resume authorized push with current credentials; no agent credential changes.

L17. User explicitly authorized RSA key /home/marcos/.ssh/id_rsa. SSH authenticated as ciltocruz; origin changed to SSH and fix/container-lifecycle pushed successfully. No token/credential content shown.
L18. CI on b06be50: hassfest success (40682884465), pytest exit1 with 2 failed/101 passed (40682884489), HACS failed Issues disabled/no topics (40682884541). Monitoring shell returned0 despite child failures; child exit codes were retained and failures were not treated as success. Baseline master 19cfe658 also failed pytest and HACS; exact baseline failure logs were unavailable, so identical pytest failures not independently established.
L19. User confirmed reviewer model already configured ("Está puesto sol6.1 macho"). Existing routing worked without changes; resilience/readability/reliability captured in order. Native review-a3e5616d78d2c3eb approved and acknowledged for target16f01e7d, authority burned; no STATUS after acknowledgement.
L20. User approved enabling Issues and adding topics home-assistant, hacs, docker and integration while preserving existing topics. API readback confirmed Issues enabled and topics set. Corrected only two obsolete config-flow tests to submit containers_mode before container names; no config-flow runtime behavior changed. Pre-fix RED is CI 2 failures; local 22 standalone tests/compileall/diff check pass; full suite awaits CI.
