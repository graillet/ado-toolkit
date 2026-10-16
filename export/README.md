# Exportador de elementos de trabajo de Azure DevOps

## Objetivo

`export_us.py` consulta los elementos de trabajo de un proyecto de Azure DevOps y genera un archivo Markdown para revisión, documentación o análisis. Aunque el nombre del script hace referencia a historias de usuario, permite exportar cualquier tipo de elemento configurado, como `User Story`, `Task` o `Bug`, siempre que exista en el proyecto.

El script solo consulta Azure DevOps; el resultado se guarda localmente.

## Requisitos

- Python 3.8 o posterior. La conversión opcional de HTML requiere las dependencias de `export/requirements.txt`; con la conversión desactivada basta la biblioteca estándar.
- Azure CLI disponible en `PATH` como `az` o `az.cmd`, con la extensión `azure-devops` instalada.
- Autenticación de Azure CLI preparada para Azure DevOps y permisos de lectura sobre el proyecto y sus elementos de trabajo. El script usa las credenciales disponibles para la CLI; no inicia sesión ni solicita credenciales.
- Permisos locales para crear archivos temporales y escribir en `export/out/`.

Puedes comprobar las herramientas con:

```powershell
python --version
az --version
az devops --help
```

Para habilitar la conversión de HTML, instala las dependencias desde la raíz del repositorio. En PowerShell puedes usar un entorno virtual local:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r export/requirements.txt
.\.venv\Scripts\python.exe export/export_us.py
```

Usa ese mismo intérprete para ejecutar el exportador y las pruebas. Si ya tienes un entorno activo, basta con `python -m pip install -r export/requirements.txt`.

## Configuración

Edita `config.json`, ubicado junto al script. Se carga siempre desde esa ubicación, independientemente del directorio desde el que ejecutes el comando.

Ejemplo para exportar historias de usuario:

```json
{
  "organization": "NABusinessTechnology",
  "project": "BBU RISE",
  "work_item_type": "User Story",
  "title_filter": "",
  "filters": [],
  "exclude_sprints": "Inactive|Sprint 1|Sprint 2",
  "include_discussions": false,
  "convert_html_to_markdown": false,
  "output_file": "BBU_RISE_US.md"
}
```

| Campo | Obligatorio | Uso |
| --- | --- | --- |
| `organization` | Sí | Nombre de la organización, sin la URL. El script construye `https://dev.azure.com/<organization>`. |
| `project` | Sí | Nombre del proyecto de Azure DevOps. |
| `work_item_type` | Sí | Un único tipo de elemento de trabajo por ejecución. |
| `output_file` | Sí | Nombre del archivo de salida, preferentemente con extensión `.md`. Se guarda dentro de `out/`, junto al script; solo se utiliza el nombre final de la ruta indicada. |
| `title_filter` | No | Expresión regular para buscar en el título, sin distinguir mayúsculas. Ausente o `""` incluye todos los títulos. |
| `exclude_sprints` | No | Cadena de nombres de sprint separados por `\|`. Ausente o `""` no excluye ninguno. No acepta un arreglo JSON ni `null`. |
| `include_discussions` | No | Booleano `true` o `false`. Si se omite, se usa `false`. Activa la consulta del historial de discusiones de los elementos que pasan los filtros. |
| `convert_html_to_markdown` | No | Booleano `true` o `false`. Por defecto `false`. Convierte Description, Acceptance criteria, Comments y Discussion a Markdown con `markdownify`. |
| `filters` | No | Lista de criterios por nombre interno de campo. Todos deben cumplirse (AND). Ausente o `[]` desactiva estos criterios. |

Usa booleanos JSON sin comillas y evita comas finales o comentarios en el archivo.

### Filtros genéricos por campo

Agrega criterios a `filters` en `config.json`. Por ejemplo, para exportar items activos o nuevos asignados al analista indicado y con progreso igual a cero:

```json
"filters": [
  {"field": "System.State", "operator": "in", "value": ["Active", "New"]},
  {"field": "Custom.Analyst", "operator": "eq", "value": "ana@example.com"},
  {"field": "Custom.ProgressPercentage", "operator": "eq", "value": 0}
]
```

Reemplaza los nombres y valores por los de tu proyecto. `field` es el nombre interno exacto que aparece en la exportación (por ejemplo, `System.State`), no la etiqueta visible de ADO. Los nombres de campo distinguen mayúsculas; se ignoran espacios alrededor del nombre configurado. Un nombre inexistente se trata como campo ausente, sin consultar el catálogo del proyecto.

| Operador | Significado | `value` |
| --- | --- | --- |
| `eq` | Igual al valor | Texto, número, booleano o `null` |
| `ne` | Diferente del valor | Texto, número, booleano o `null` |
| `in` | Igual a cualquiera de los valores | Lista no vacía de valores simples |
| `not_in` | Diferente de todos los valores | Lista no vacía de valores simples |
| `contains` | El texto contiene una subcadena literal, no una regex | Texto |
| `exists` | El campo está presente, aunque sea vacío o `null` | No se permite `value` |
| `not_exists` | El campo está ausente | No se permite `value` |
| `is_empty` | Ausente, `null`, cadena vacía o solo espacios | No se permite `value` |

Todos los criterios se combinan con **AND**, también con `title_filter`, `include_sprints` y `exclude_sprints`. Usa `in` para aceptar alternativas en un mismo campo; no hay grupos OR entre campos. Se aplican a los valores descargados antes de convertir HTML y antes de consultar discusiones. No reducen la descarga inicial.

Las comparaciones de texto ignoran mayúsculas por defecto. Puedes exigir coincidencia de mayúsculas por criterio:

```json
{"field": "Custom.Code", "operator": "eq", "value": "OSE", "case_sensitive": true}
```

Se conservan los tipos: `0` es diferente de `"0"` y de `false`; `0` y `0.0` son numéricamente iguales. Los valores de texto no se recortan. `contains` solo opera sobre texto; una cadena vacía coincide con cualquier texto. En campos de identidad como `Custom.Analyst` o `System.AssignedTo`, las comparaciones usan `uniqueName` (normalmente correo), no `displayName`. Los objetos sin `uniqueName` y las listas no coinciden con operadores de comparación; sí pueden consultarse con los operadores de presencia.

Si el campo **no existe** en un item, no coincide con `eq`, `ne`, `in`, `not_in` ni `contains`. Solo `not_exists` e `is_empty` aceptan esa ausencia. Un campo presente con `null` puede coincidir con `eq` y `value: null`. `0` y `false` no se consideran vacíos.

Ejemplos independientes:

```json
{"field": "System.Tags", "operator": "contains", "value": "OSE"}
```

```json
{"field": "Custom.Analyst", "operator": "not_exists"}
```

```json
{"field": "Custom.Approval", "operator": "is_empty"}
```

Cada ejemplo es un criterio que va dentro de la lista `filters`. Para desactivar todos estos filtros usa `"filters": []`. No se aceptan `null`, objetos en lugar de listas, operadores desconocidos ni valores incompatibles. Los errores indican el índice, por ejemplo `filters[0]`, y detienen la ejecución antes de consultar ADO o escribir la salida. Si ningún item cumple, se genera solo el encabezado general.

### Convertir HTML a Markdown

```json
"convert_html_to_markdown": true
```

Elimina estilos de presentación y contenedores vacíos, conservando texto, listas, enlaces, tablas y tachados (`<s>`, `<del>` y `<strike>` se convierten en `~~texto~~`). En tablas sin encabezados HTML explícitos se utiliza la primera fila como encabezado. También corrige listas anidadas pegadas desde ADO y conserva los espacios entre palabras y los bloques de código.

Se aplica al generar Description, Acceptance criteria, Comments y las entradas de Discussion. Los textos sin etiquetas HTML se conservan tal cual, y las viñetas de otros campos mantienen su formato. Los valores descargados no se modifican. Con `false` o si se omite, se conserva el HTML original y no se necesita instalar `markdownify`. Valores como `"true"` entre comillas o `null` son inválidos.

Markdown no conserva colores, fuentes ni toda la disposición visual de HTML; las tablas con celdas combinadas o estructuras complejas pueden requerir revisión. Los enlaces de imágenes se conservan, pero las imágenes no se descargan. La conversión tampoco activa la consulta de discusiones: eso depende de `include_discussions`.

### Campos exportados

Siempre se descargan todos los campos que ADO devuelve para cada item, incluidos los personalizados. No se necesita `include_all_fields`; si una configuración antigua lo contiene, se ignora.

Los campos se presentan juntos como viñetas debajo del título: primero los habituales y luego los demás, ordenados por nombre interno. Por ejemplo:

```markdown
- **State:** Active
- **Custom.Analyst:** ana@example.com
- **Custom.ProgressPercentage:** 0
```

No se repiten los campos ya representados en el encabezado o en las secciones de descripción, criterios de aceptación y comentarios. Tampoco hay una sección separada de campos adicionales. `System.History` se excluye siempre de las viñetas; sus entradas se consultan para la sección `Discussion` únicamente cuando `include_discussions` es `true`.

Los objetos con `uniqueName` se muestran solo con ese valor, incluso dentro de listas y objetos anidados. Los objetos sin `uniqueName` y las listas se representan como JSON compacto en la misma viñeta. Los valores `0`, `false`, `null` y las cadenas vacías (`""`) se conservan. Los textos de estos campos se escapan para Markdown y los saltos de línea continúan dentro de la misma viñeta. Assigned to conserva su formato habitual.

Se incluyen los campos presentes en la respuesta de cada item; los items pueden tener campos distintos. Las relaciones y los adjuntos no forman parte de estos campos. El historial de discusiones sigue dependiendo de `include_discussions`. Para actualizar un archivo ya generado, vuelve a ejecutar el exportador.

Referencia de la consulta: [Work Items - Get Work Items Batch](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/get-work-items-batch?view=azure-devops-rest-7.1).

### Filtro de títulos

El filtro busca una coincidencia en cualquier parte del título. Por ejemplo:

- `"design"`: incluye títulos que contienen `design`, sin distinguir mayúsculas.
- `"design|architecture"`: incluye títulos que contienen cualquiera de esas palabras.
- `"^Design"`: incluye títulos que comienzan con `Design`.

En este campo, `|` es un operador de expresión regular. No es necesario agregar `(?i)` porque el script ya ignora mayúsculas. Si una expresión usa barras invertidas, deben escaparse en JSON: por ejemplo, `"\\bdesign\\b"`.

### Incluir sprints

Para exportar solo ciertos sprints, agrega a `config.json`:

```json
"include_sprints": "Sprint 1 | Sprint 2"
```

Compara el nombre exacto del sprint (el último segmento de `System.IterationPath`), sin distinguir mayúsculas e ignorando espacios alrededor de los nombres. No compara rutas completas ni incluye descendientes. Los elementos sin ruta no coinciden.

Si se omite o vale `""`, permite todos los sprints. Debe ser una cadena, no una lista ni `null`. Se combina con el filtro de títulos y con `exclude_sprints`; si un sprint aparece en ambas listas, queda excluido. Se aplica antes de consultar discusiones.

### Exclusión de sprints

```json
"exclude_sprints": "Inactive | Sprint 1 | Sprint 2"
```

Aquí `|` separa nombres literales, no expresiones regulares. Se ignoran mayúsculas, espacios alrededor de cada entrada y entradas vacías.

La comparación es exacta contra el último segmento de `System.IterationPath`:

- `Proyecto\Release\Sprint 1` queda excluido por `Sprint 1`.
- `Proyecto\Release\Sprint 10` permanece incluido.
- `Proyecto\Sprint 1\Subiteración` permanece incluido: su último segmento es `Subiteración`.

Un mismo nombre se excluye en todas las ramas de iteración del proyecto. No se comparan rutas completas ni se excluyen descendientes automáticamente. Los elementos sin ruta de iteración permanecen incluidos. No existe una opción de consola para cambiar este filtro; edita `config.json`.

## Ejecución

Desde la raíz del repositorio:

```powershell
python export/export_us.py
```

Desde la carpeta `export`:

```powershell
python export_us.py
```

Para reemplazar el filtro de títulos de la configuración solo durante una ejecución:

```powershell
python export/export_us.py --title-filter="design|architecture"
```

Para desactivar el filtro de títulos, incluso si está definido en `config.json`:

```powershell
python export/export_us.py --title-filter=
```

Este argumento no desactiva la exclusión de sprints. Usa comillas alrededor de expresiones con espacios o `|` para que la consola las pase como un solo argumento.

Consulta la ayuda con:

```powershell
python export/export_us.py --help
```

## Archivo generado

Con el ejemplo anterior, el resultado se guarda en `export/out/BBU_RISE_US.md`. La carpeta se crea si no existe y un archivo con el mismo nombre se sobrescribe al completar la exportación. El formato siempre es Markdown en UTF-8, independientemente de la extensión elegida.

Cada elemento incluye:

- ID y título.
- Tipo, estado y motivo (`System.Reason`, cuando está disponible).
- Persona asignada, ruta de iteración y ruta de área.
- Enlace al elemento en Azure DevOps.
- Descripción, criterios de aceptación y el campo `Microsoft.VSTS.CMMI.Comments`, si tienen contenido.
- Discusiones con autor y fecha, cuando están habilitadas y se encuentran entradas en el historial.

Los campos de texto conservan el HTML original salvo que se active `convert_html_to_markdown`. No se descargan archivos adjuntos ni imágenes. El campo `Comments` y la sección `Discussion` provienen de fuentes distintas: desactivar discusiones no elimina el campo `Comments`.

Si no hay coincidencias, se genera un archivo que contiene únicamente el encabezado general.

## Cómo funciona

1. Lee la configuración y los argumentos de consola.
2. Consulta los IDs del proyecto y tipo elegidos, en páginas de hasta 1,000 elementos ordenados por ID ascendente.
3. Descarga los campos de esos elementos en lotes de hasta 200.
4. Aplica localmente el filtro de títulos, la inclusión y exclusión de sprints y los criterios de `filters`.
5. Si se habilitaron discusiones, consulta las actualizaciones de cada elemento restante en páginas de 200 y extrae los valores nuevos de `System.History`.
6. Construye y escribe el Markdown, conservando el orden recibido de los lotes de elementos.

Los filtros no reducen la descarga inicial de elementos; sí evitan consultar discusiones de elementos descartados. Activar discusiones agrega consultas por elemento y puede aumentar el tiempo de ejecución. La sección de discusiones refleja las entradas encontradas en `System.History`; no es una exportación de toda la API de comentarios.

Los archivos JSON temporales usados para las solicitudes se eliminan al terminar cada petición, incluso si esta falla. El script detecta IDs omitidos en los lotes y páginas de IDs que no avanzan, y detiene la ejecución para evitar guardar una exportación incompleta en esos casos. No implementa reintentos automáticos.

## Problemas frecuentes

| Mensaje o síntoma | Qué revisar |
| --- | --- |
| `Azure CLI was not found in PATH.` | Instalación de Azure CLI y disponibilidad de `az` o `az.cmd` en la terminal actual. |
| Error de `az devops` | Extensión `azure-devops`, autenticación, permisos y nombres de organización/proyecto. El script propaga el error de la CLI. |
| `Missing required config field(s)` | Campos obligatorios ausentes o vacíos en `config.json`. |
| Un filtro numérico no encuentra coincidencias | Revisa el tipo de `value`: para `Custom.ProgressPercentage` usa `100` sin comillas, no `"100"`. Los filtros no convierten textos en números. |
| `HTML conversion requires markdownify` y el Markdown conserva items anteriores | Ejecuta con `.\.venv\Scripts\python.exe export/export_us.py` si instalaste las dependencias en `.venv`, o instálalas en el intérprete que utilizas. La ejecución se detiene antes de actualizar el archivo. |
| Error al leer JSON | Sintaxis y codificación UTF-8 de `config.json`. |
| `exclude_sprints must be a string...` | Usa una cadena separada por `\|`, no una lista ni `null`. |
| Error de expresión regular | Sintaxis de `title_filter` y escape de barras invertidas en JSON. |
| `Export incomplete: Azure DevOps omitted work item IDs...` | Disponibilidad y permisos de lectura de los IDs indicados. |
| `Azure DevOps returned a non-advancing work item page.` | La consulta devolvió IDs que no avanzan en orden ascendente; la ejecución se interrumpe. |
| Archivo vacío salvo el encabezado | Tipo elegido, filtro de títulos y nombres de sprints excluidos. |

Si una consulta falla antes de la escritura final, un archivo de salida de una ejecución anterior puede seguir existiendo. Comprueba que la consola termine con `Done: <ruta>` antes de considerar actualizado el resultado.

## Verificación local

Desde la raíz del repositorio:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Las pruebas usan respuestas simuladas y no requieren conectarse a Azure DevOps. Cubren paginación, limpieza de archivos temporales, detección de elementos omitidos, prioridad del filtro de consola y exclusión de sprints.
