# Exportador de elementos de trabajo de Azure DevOps

## Objetivo

`export_us.py` consulta los elementos de trabajo de un proyecto de Azure DevOps y genera un archivo Markdown para revisión, documentación o análisis. Aunque el nombre del script hace referencia a historias de usuario, permite exportar cualquier tipo de elemento configurado, como `User Story`, `Task` o `Bug`, siempre que exista en el proyecto.

El script solo consulta Azure DevOps; el resultado se guarda localmente.

## Requisitos

- Python 3.8 o posterior. Solo utiliza módulos de la biblioteca estándar; no requiere instalar paquetes con `pip`.
- Azure CLI disponible en `PATH` como `az` o `az.cmd`, con la extensión `azure-devops` instalada.
- Autenticación de Azure CLI preparada para Azure DevOps y permisos de lectura sobre el proyecto y sus elementos de trabajo. El script usa las credenciales disponibles para la CLI; no inicia sesión ni solicita credenciales.
- Permisos locales para crear archivos temporales y escribir en `export/out/`.

Puedes comprobar las herramientas con:

```powershell
python --version
az --version
az devops --help
```

## Configuración

Edita `config.json`, ubicado junto al script. Se carga siempre desde esa ubicación, independientemente del directorio desde el que ejecutes el comando.

Ejemplo para exportar historias de usuario:

```json
{
  "organization": "NABusinessTechnology",
  "project": "BBU RISE",
  "work_item_type": "User Story",
  "title_filter": "",
  "exclude_sprints": "Inactive|Sprint 1|Sprint 2",
  "include_discussions": false,
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

Usa booleanos JSON sin comillas y evita comas finales o comentarios en el archivo.

### Filtro de títulos

El filtro busca una coincidencia en cualquier parte del título. Por ejemplo:

- `"design"`: incluye títulos que contienen `design`, sin distinguir mayúsculas.
- `"design|architecture"`: incluye títulos que contienen cualquiera de esas palabras.
- `"^Design"`: incluye títulos que comienzan con `Design`.

En este campo, `|` es un operador de expresión regular. No es necesario agregar `(?i)` porque el script ya ignora mayúsculas. Si una expresión usa barras invertidas, deben escaparse en JSON: por ejemplo, `"\\bdesign\\b"`.

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

Los campos de texto se copian tal como los devuelve Azure DevOps. No se convierte el HTML a Markdown ni se descargan archivos adjuntos o imágenes. El campo `Comments` y la sección `Discussion` provienen de fuentes distintas: desactivar discusiones no elimina el campo `Comments`.

Si no hay coincidencias, se genera un archivo que contiene únicamente el encabezado general.

## Cómo funciona

1. Lee la configuración y los argumentos de consola.
2. Consulta los IDs del proyecto y tipo elegidos, en páginas de hasta 1,000 elementos ordenados por ID ascendente.
3. Descarga los campos de esos elementos en lotes de hasta 200.
4. Aplica el filtro de títulos y después la exclusión de sprints localmente.
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
python -B -m unittest discover -s tests -v
```

Las pruebas usan respuestas simuladas y no requieren conectarse a Azure DevOps. Cubren paginación, limpieza de archivos temporales, detección de elementos omitidos, prioridad del filtro de consola y exclusión de sprints.
