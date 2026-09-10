# Dev Autopilot: plan hacia 1.0.0 y portfolio de Agentic AI

Fecha: 9 de septiembre de 2026. Repositorio: rsolerortuno/dev-autopilot. Revisión: `d76bc5fd8dd14798c8bf509416557cdaed71d36b`, versión declarada 0.6.0. Este documento es una propuesta de ejecución; sus objetivos y umbrales todavía no son logros alcanzados.

## Decisión recomendada

Convertir Dev Autopilot en una plataforma demostrable de desarrollo autónomo **acotado, recuperable y evaluable**, con especialización en software científico. Conservar esa especialización: da coherencia al producto y diferencia el portfolio.

La base actual merece continuar. Para 1.0.0 priorizar fiabilidad, seguridad, evaluación empírica y experiencia de instalación. Para la candidatura profesional añadir MCP, recuperación de contexto y una comparación medible entre estrategias. No intentar cubrir literalmente todos los puestos de IA: entrenamiento de modelos, visión, voz y operaciones de inferencia a gran escala son especializaciones distintas.

La promesa profesional debe ser: «Puedo diseñar, medir y operar agentes que usan herramientas, se recuperan de fallos y producen resultados revisables». Un repositorio generado por agentes no acredita por sí solo esa capacidad: hacen falta decisiones explicadas por ti, experimentos y una demostración que puedas defender.

## Qué existe y qué falta

| Competencia | Evidencia actual | Próxima evidencia necesaria |
|---|---|---|
| Orquestación persistente | SQLite, máquina de estados, DAG de hitos, reintentos | Recuperación probada con terminación abrupta y compatibilidad de datos |
| Coordinación de roles | Implementación, auditoría y revisión; revisión ligada al diff | Comparación contra un solo agente; medición de errores que detecta cada rol |
| Herramientas y contratos | Bridges de CLI, Pydantic y JSON Schema | Contratos de proveedores versionados y conformance tests; integración MCP |
| Seguridad | Gates de alcance, protección Git, threat model | Aislamiento preventivo, entorno mínimo para agentes, pruebas de inyección |
| Workers | Checkpoints, heartbeats y fencing sobre almacenamiento | Coordinación atómica o límite explícito a un coordinador; pruebas de carreras |
| Observabilidad | Eventos persistidos, progreso y bundles | Trazas correlacionadas, latencia, consumo y errores por etapa/proveedor |
| Evals | Pruebas de software offline con agentes falsos | Dataset, ejecución real controlada, baselines y resultados repetidos |
| Memoria/RAG | Contexto acotado y estado persistente | Recuperación de código/documentación con procedencia y evaluación propia |
| Distribución | Wheel, CI, CodeQL, auditoría de dependencias y SBOM configurados | Gates del SHA de release, SBOM del producto instalado, instalación limpia |
| Portfolio | Arquitectura, ADR y caso TargetIntel descrito | Demo reproducible, resultados accesibles y explicación personal de decisiones |

El estado persistente no equivale a memoria semántica; los tests con fake agents no equivalen a evaluación de calidad del modelo; varios roles secuenciales no equivalen a ejecución paralela de hitos. Son capacidades distintas.

## Hallazgos que condicionan 1.0.0

1. **Aislamiento y secretos — prioridad alta.** `ExecutableAgentAdapter.execute` copia `os.environ`. La allowlist del worker no se aplica a este camino. La protección de Git detecta cambios; no impide que un proceso con permisos del host lea secretos o escriba fuera del repositorio. La documentación reconoce que no hay sandbox completo. Añadir entorno mínimo por rol, redacción de logs y ejecución aislada con permisos y red controlados.
2. **Coordinación Drive — prioridad alta.** `DriveQueue._finish` hace `assert_ownership`, escribe el resultado terminal y elimina claves compartidas en operaciones separadas. `heartbeat` también lee y escribe sin CAS. Un takeover entre esas operaciones puede invalidar la garantía pretendida. Es una carrera identificada por inspección, pendiente de reproducción. Los namespaces de artefactos no bastan para hacer atómico el estado terminal. Resolver con un coordinador transaccional o limitar formalmente la coordinación soportada; conservar Drive como almacenamiento de blobs.
3. **Compatibilidad del runtime.** `_terminate_group` usa `os.killpg` y señales POSIX; CI solo declara Ubuntu. Recomiendo Linux/WSL2 como plataforma soportada de 1.0.0 y un diagnóstico temprano en plataformas incompatibles. Soportar Windows nativo requiere gestión y prueba del árbol de procesos; no basta con que el paquete se instale.
4. **Release desacoplada de validación.** `release.yml` construye y sube artefactos, pero no ejecuta ni depende explícitamente de los gates de CI del mismo SHA. El SBOM se genera sobre el entorno del builder, sin instalar primero el producto y sus dependencias. Reparar ambas cosas.
5. **Evidencia antigua.** `VALIDATION.md` conserva los 155 tests y 84,08 % de 0.5.0. El changelog declara validación real de 0.6.0 con TargetIntel; esa declaración no sustituye a un bundle reproducible inspeccionado. Actualizar evidencia por SHA y distinguir pruebas offline, proveedores reales y servicios externos.
6. **Sin presupuesto económico observable.** Hay límites de intentos, timeout y tamaño de contexto. No he encontrado contabilidad de tokens/coste ni presupuesto agregado por proyecto en el código revisado. Añadirlo con valores desconocidos explícitos cuando un CLI no informe consumo; no convertir ausencia de datos en coste cero.
7. **Planificación secuencial.** `ContinuousProjectRunner.run` recorre el orden topológico de hitos y los ejecuta uno a uno. Es razonable para integridad del repositorio. Añadir paralelismo de desarrollo mediante worktrees; no prometer un scheduler paralelo del producto hasta construirlo y medirlo.

## Señales del mercado utilizadas

Muestra orientativa de ofertas consultadas; no es un censo ni prueba de demanda de todas las empresas o de un mercado geográfico concreto.

- Accenture pide experiencia con agentes, RAG, herramientas/frameworks y MCP, además de diseño de sistemas, errores, observabilidad y soporte de producción. [Oferta Lead Agentic AI Engineer](https://www.accenture.com/us-en/careers/jobdetails?id=R00335391_en).
- Scale AI pide integración y operación de sistemas de IA, herramientas/MCP, retrieval o bases vectoriales y evaluación, regresiones y trazas. [Oferta Senior Frontier Agents Engineer](https://scale.com/careers/4694863005).
- Bedrock Robotics explicita orquestación, integración de herramientas/MCP, evaluación, observabilidad y despliegue en AWS. [Oferta Agentic Platforms & Agents Engineer](https://jobs.ashbyhq.com/bedrock-robotics/9e53a488-7c7e-4782-922d-a2f8d393b3b8).

Conclusión de esta muestra: el mayor retorno para este portfolio está en demostrar funcionamiento medible y operación, junto a integración de herramientas. No hay razón para reescribir el motor en LangGraph solo para añadir una palabra al README. Un ADR que compare el motor propio con alternativas y una integración pequeña pueden demostrar comprensión sin una migración costosa.

## Objetivos y achievements

Un achievement se desbloquea solo cuando su evidencia está versionada y revisada. Los siguientes umbrales son objetivos propuestos, no cifras actuales ni estándares del sector.

| Hito | Objetivo | Criterio verificable de logro | Dependencias |
|---|---|---|---|
| M06 — Base verificable | Poder confiar en los gates | CI completo en SHA candidato; cobertura ≥80 % existente; wheel instalado fuera del árbol; informe actualizado; plataforma soportada explícita | Ninguna |
| M07 — Autonomía acotada | Contener errores y consumo | Suite de ≥20 casos adversariales: todos bloquean o degradan como se especifica; secretos señuelo ausentes de logs; límites de tiempo/llamadas activos y persistentes | M06 |
| M08 — Recuperación fiable | No perder ni publicar resultados obsoletos | ≥100 intercalados deterministas de claims/heartbeats/takeover/publicación sin publicación obsoleta ni borrado del lease vigente; recuperación tras kill en cada frontera persistente | M06 |
| M09 — Agentes medibles | Saber cuándo funciona y qué cuesta | Dataset de 30 tareas de 3 categorías; baseline fijada antes de comparar; 3 repeticiones por configuración; ≥24/30 éxitos en al menos 2 repeticiones; coste/latencia/correcciones reportados | M07, M08 |
| M10 — Integración agentic | Demostrar MCP y contexto recuperado | ≥3 herramientas MCP con permisos; 20 consultas con referencia humana; Recall@5 objetivo ≥0,8; inyección y acceso fuera de alcance rechazados | M07; contratos de M09 |
| M11 — Operación y demo | Otra persona puede reproducirlo | Instalación y demo offline ≤10 min en entorno documentado; smoke real de proveedores con evidencia; sesión de resistencia de 8 h con fallos inyectados y límites activos | M08, M09, M10 |
| M12 — Release y defensa | Entregar una 1.0.0 defendible | Todos los gates; cero hallazgos críticos/altos abiertos; migración probada; artefactos verificables; vídeo y 3 casos con métricas; revisión humana final | Todos |

Si falla el umbral de calidad de M09, analizar por categoría y corregir. No cambiar el dataset de evaluación para esconder fallos. Si el alcance de tareas es demasiado ambicioso, redefinir públicamente la promesa del producto y repetir la evaluación con un conjunto reservado.

## Backlog ejecutable por agentes

Cada fila es un paquete que puede dividirse en PRs pequeños. El dueño de integración asigna archivos y congela los contratos compartidos antes del paralelismo.

| ID | Trabajo / superficie | Entregable y aceptación específica | Depende de |
|---|---|---|---|
| T01 | Baseline y plataformas | Informe del SHA; gates reproducibles; Linux/WSL2 soportado y doctor claro | — |
| T02 | Release CI y SBOM | Release bloqueada por tests/lint/tipos/build; SBOM incluye dependencias reales del wheel; tag y versión coinciden | T01 |
| T03 | API/CLI/esquemas/DB | Política SemVer; schemas sincronizados por test; fixtures 0.6.0 migran o fallan sin pérdida; backup/restore | T01 |
| T04 | Entorno y logs | Allowlist por adaptador, secretos señuelo y redacción de stdout/stderr/evidencia | T01 |
| T05 | Sandbox | Runtime aislado, mounts mínimos, límites CPU/RAM/tiempo y política de red; escape de ruta bloqueado | T04 |
| T06 | Política y aprobación | Permisos por herramienta/rol; aprobación ligada a acción+diff+identidad; modificación invalida autorización | T05 |
| T07 | Coordinación | Reproducir carrera; ADR; coordinación transaccional local/single host como base; Drive conserva blobs | T01 |
| T08 | Recuperación | Tests de caída durante claim/checkpoint/publicación y limpieza; fencing aplicado en escritura autorizada | T07 |
| T09 | Providers | Contrato común, capacidades/versiones, clasificación de errores, fixtures de respuestas reales redactadas | T03 |
| T10 | Telemetría | Spans proyecto/hito/rol/herramienta/retry; exportación OpenTelemetry opcional; sin secretos | T04, T09 |
| T11 | Presupuestos | Límites proyecto/hito; reservar antes de llamada; recuperar estado tras restart; consumo desconocido visible | T09, T10 |
| T12 | Dataset y harness | 30 tareas congeladas con oracle, fixtures, metadata y separación desarrollo/evaluación; informe JSON+HTML | T01 |
| T13 | Comparación agentic | Misma base/modelo/presupuesto: single agent frente a pipeline con reviewer; ablación de auditoría; fallos y variación reportados | T08, T11, T12 |
| T14 | MCP | Herramientas de inspección de repo, consulta de estado y recuperación de evidencia; esquemas, timeout y permisos; sin shell arbitraria | T06, T09 |
| T15 | Retrieval | Índice por commit, procedencia archivo/línea/hash, exclusión de secretos; comparación lexical frente a embeddings opcionales | T04, T12 |
| T16 | Contexto y memoria | Contexto recuperado separado de instrucciones; invalidación por commit; límite de contexto y evaluación de utilidad | T15 |
| T17 | Paquete de demo | Comando offline, dataset pequeño, bundle de ejemplo y recorrido de fallo→recuperación→revisión | T08, T10 |
| T18 | Smoke real | Ejecución acotada con proveedores instalados; modelo y CLI exactos; resultado y consumo disponible documentados | T09, T11, T17 |
| T19 | Drive/Colab | Smoke con credenciales en entorno preparado; checkpoint, interrupción y reanudación; evidencia redactada | T08, T18 |
| T20 | Despliegue | Imagen reproducible Linux, health/estado, almacenamiento persistente, runbook de backup y recuperación | T05, T08, T10 |
| T21 | Portfolio | README corto, arquitectura, matriz competencia→evidencia, 3 casos y vídeo de 5–8 minutos | T13, T14, T16, T18 |
| T22 | Release candidate | Instalación limpia, gates mismo SHA, migración, changelog, checksums y provenance; revisión final | T02–T21 |

Orden por olas:

1. **Ola 0:** T01; después contratos T03 y diseño de T07. Fijar la definición de 1.0.0 antes de aumentar agentes.
2. **Ola 1:** seguridad T04–T06; fiabilidad T07–T08; evaluación offline T12 y release T02 en paquetes sin colisiones.
3. **Ola 2:** proveedores/telemetría/presupuestos T09–T11; MCP T14; retrieval T15–T16. Un solo dueño de contratos compartidos.
4. **Ola 3:** evaluación T13; demo/smoke T17–T19; despliegue T20.
5. **Ola 4:** portfolio T21 e integración final T22.

No doy una fecha fiable de finalización antes de T01 y de dos paquetes reales: el paralelismo no reduce proporcionalmente revisiones, experimentos con modelos ni dependencias. Tras esos paquetes, estimar por tiempo mediano observado y trabajo restante, reservando tiempo explícito para integración y repetición de evals.

## Qué posponer

Fuera de 1.0.0: SaaS multiusuario, facturación, Kubernetes, entrenamiento/fine-tuning, voz, frontend grande y scheduler distribuido general. A2A puede añadirse después si una integración concreta lo necesita. Para puestos cloud, desplegar la demo en un proveedor y explicar IAM, secretos y recuperación aporta más que listar tres nubes sin operarlas.

Si se desea una release mínima antes del portfolio completo, se puede sacar un candidato con M06–M09 y M11–M12 adaptados, dejando MCP/RAG explícitamente para 1.1. Para el objetivo profesional expresado recomiendo conservar M10 en el alcance.

## Cómo trabajar aquí con Luna

Sí: el entorno de esta conversación ofrece `gpt-5.6-luna` para subagentes. El límite de concurrencia expuesto en esta sesión es **4 agentes contando al coordinador**, por lo que caben este coordinador y hasta 3 Luna simultáneos. Es capacidad de este entorno, no una garantía universal para toda cuenta ni confirmación de que ese alias exista en tu CLI externo.

Reparto recomendado: Luna A implementa un paquete de runtime; Luna B trabaja en una superficie independiente (evals/integración); Luna C revisa o desarrolla otro paquete aislado. El coordinador decide arquitectura, integra y verifica el resultado. La revisión cambia de agente respecto del implementador; compartir modelo no garantiza independencia de errores, por lo que se mantienen oracles y gates deterministas.

Cada agente necesita un worktree y rama propios. Los subagentes de esta conversación comparten filesystem: asignar rutas distintas antes de ejecutar evita colisiones. Mantener un único integrador para `models.py`, esquemas, DB y contratos. No compartir un SQLite de prueba entre worktrees.

Se puede trabajar de forma continuada dentro de límites, pero «sin parar» no significa disponibilidad infinita: cuotas de cuenta, credenciales, procesos, suspensión del equipo y fallos pueden detenerlo. Guardar backlog y evidencias permite retomar. Las tareas programadas, si se configuran después, no eliminan esos límites. No se han iniciado agentes ni automatizaciones durante este análisis.

La documentación oficial explica la delegación y recomienda instrucciones explícitas sobre cuándo delegar y cuánto verificar: [OpenAI, guía de modelos](https://developers.openai.com/api/docs/guides/latest-model). La disponibilidad concreta de Luna y las cuatro plazas proceden de las herramientas de esta sesión, no de una inferencia de esa página.

### Contrato para cada paquete

```text
Objetivo: completar Txx del PLAN-1.0.0 sobre el SHA base asignado.
Lee las instrucciones aplicables del repositorio y trabaja en tu worktree.
Archivos asignados: [lista concreta]. Contratos congelados: [lista].
Criterios de aceptación: [copiar criterios del paquete].
Incluye pruebas de comportamiento y fallos relevantes, no solo mocks felices.
No alteres criterios, suprimas gates ni declares éxito sin evidencia.
Entrega: cambios, comandos y resultados, riesgos y SHA/diff revisado.
Si dependes de otro paquete, devuelve el bloqueo concreto y conserva el estado.
No publiques ni fusiones la release; el integrador revisa el resultado.
```

Prompt de arranque sugerido para el siguiente paso:

> Ejecuta el plan de Dev Autopilot hasta 1.0.0 con este coordinador y hasta tres subagentes gpt-5.6-luna. Empieza por T01 y fija contratos antes de paralelizar. Usa worktrees separados, gates por paquete, revisión independiente e integración secuencial. Persiste el backlog y las evidencias. Avanza en trabajo desbloqueado hasta completar el alcance; no publiques la release. No hagas llamadas de pago nuevas sin presupuesto explícito; prepara los smokes que requieran credenciales como pasos pendientes concretos.

## Qué debes poder explicar tú

- Por qué usar máquina de estados y cuándo introducir planificación dinámica.
- Por qué un checksum no autentica el origen y por qué un token sin escritura condicional no garantiza fencing.
- Cómo separas datos recuperados de instrucciones confiables.
- Qué errores detecta el reviewer, cuáles comparte con el implementador y qué dicen los experimentos.
- Cuánto cuesta resolver una tarea y qué se sacrifica al reducir presupuesto.
- Qué ocurre exactamente si un proceso muere entre persistencia y publicación.

Graba tres casos: tarea resuelta, fallo recuperado e intento inseguro bloqueado. Acompaña cada uno de configuración, trazas redactadas, resultado verificable y una decisión de diseño explicada por ti.

## Verificación ejecutada en esta revisión

Se clonó el repositorio y se creó un entorno virtual aislado con CPython 3.12 sobre Windows; instalación editable con extras `dev` completada. No se modificó el código del repositorio.

- `pytest -q --tb=short --maxfail=5`: **126 passed, 5 failed**, parada al quinto fallo en 20,56 segundos. No es una ejecución completa ni una cifra de cobertura. Los cinco fallos observados son de split/storage: `PermissionError` al reabrir archivos temporales en Windows, tanto en CLI como en tests unitarios. Logs en `pytest-audit-windows.txt` junto a este plan. Añadir este caso a T01: corregir si Windows es soportado; de lo contrario, declarar Linux/WSL2 y rechazar la operación con diagnóstico claro.
- `ruff check . --output-format concise`: **All checks passed**, con Ruff 0.16.6.
- `mypy`: **7 errores en 2 archivos**, con mypy 2.3.1 sobre Windows; referencias a `os.killpg`, `signal.SIGKILL` y `os.sysconf` en adaptador y worker. Son dependencias POSIX, no evidencia de que el gate Linux falle. Log en `mypy-audit-windows.txt`.
- La consulta de las últimas tres ejecuciones del workflow CI devolvió dos éxitos en SHA `589b54f...` y un fallo anterior. No confirma CI completo en el HEAD `d76bc5f...`. CodeQL sí figura exitoso en HEAD. [CI exitoso consultado](https://github.com/rsolerortuno/dev-autopilot/actions/runs/31724026693), [CodeQL en HEAD](https://github.com/rsolerortuno/dev-autopilot/actions/runs/34099830347).
- No se ejecutaron proveedores reales, sesiones autenticadas Drive/Colab, benchmark de agentes ni la carrera de concurrencia propuesta. No se ha validado aquí todo Linux ni la matriz Python del proyecto.

## Referencias de implementación auditadas

- [Adaptador subprocess](https://github.com/rsolerortuno/dev-autopilot/blob/d76bc5fd8dd14798c8bf509416557cdaed71d36b/src/dev_autopilot/adapters/subprocess.py).
- [Cola y publicación](https://github.com/rsolerortuno/dev-autopilot/blob/d76bc5fd8dd14798c8bf509416557cdaed71d36b/src/dev_autopilot/worker/queue.py).
- [Project runner](https://github.com/rsolerortuno/dev-autopilot/blob/d76bc5fd8dd14798c8bf509416557cdaed71d36b/src/dev_autopilot/project.py).
- [Release workflow](https://github.com/rsolerortuno/dev-autopilot/blob/d76bc5fd8dd14798c8bf509416557cdaed71d36b/.github/workflows/release.yml).
- [Threat model](https://github.com/rsolerortuno/dev-autopilot/blob/d76bc5fd8dd14798c8bf509416557cdaed71d36b/docs/threat-model.md).
