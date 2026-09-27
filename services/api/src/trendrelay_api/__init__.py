"""TrendRelay API package.

Importing this points the process's scratch and model caches at the project's
own drive. It is done here rather than in each entry point because it has to
happen before anything renders, downloads or loads a model, and every one of
those paths begins by importing something from this package - the API, the
worker, the MCP server, a test. See `project_storage`.
"""

from trendrelay_api.project_storage import keep_work_on_the_project_drive

__version__ = "0.1.0"

keep_work_on_the_project_drive()
