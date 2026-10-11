"""TrendRelay API package.

Importing this points the process's scratch and model caches at the project's
own drive. It is done here rather than in each entry point because it has to
happen before anything renders, downloads or loads a model, and every one of
those paths begins by importing something from this package - the API, the
worker, the MCP server, a test. See `project_storage`.

It also caps OpenBLAS's thread pool before NumPy can load. OpenBLAS commits a
work buffer per thread at import - about 31 MB each, one per logical CPU - so
on a 20-thread machine `import numpy` alone committed 628 MB, in every one of
the API, worker, watcher and MCP processes, almost none of it ever touched.
The matrix work here is dot products and norms over a few hundred vectors,
which never parallelise anyway; four threads keep room for anything larger.
Set before the import that could load NumPy, and only as a default, so an
operator's own OPENBLAS_NUM_THREADS still wins.
"""

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")

from trendrelay_api.project_storage import keep_work_on_the_project_drive  # noqa: E402

__version__ = "0.1.0"

keep_work_on_the_project_drive()
