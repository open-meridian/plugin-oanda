"""What this version declares beside its roles (W8.1, W4.1): from contract
v25 the whole of what it says about itself.

Built from the plugin's code and used twice: `__main__` registers with it,
and `meridian plugin upload` reads the same one from the built image
(pyproject.toml's `[tool.meridian] declaration`), with the documentation's
texts beside it. A version registers exactly the declaration it was uploaded
with, or its sidecar refuses it naming the first difference, so beside the
parts it declared before (declared.py, whose docstring says what each is)
it carries its interface -- its pages, with the levels each serves, and the
tools their typed routes make -- on a port fixed here, never read from the
environment, from 0.5.0 its catalogue (declared.py), and the vendor's
documentation, the Markdown in `docs/`
(`/plugin/docs` in its image), which a deployment admin reads on its
Documentation tab before launching it.
"""

from __future__ import annotations

import meridian
from meridian.declaration import Declaration, Storage

from .declared import CATALOGUE, KINDS, NOT_CARRIED
from .page import TITLE, pages
from .settings import DECLARED, MOST_RETENTION_DAYS

#: The port its pages are served on, on loopback, where its sidecar forwards.
PORT = 8000

DECLARATION = Declaration(
    settings=DECLARED,
    not_carried=NOT_CARRIED,
    storage=Storage(retention_days=MOST_RETENTION_DAYS, kinds=KINDS),
    interface=meridian.Interface(port=PORT, title=TITLE, pages=pages),
    reads_external_accounts=True,
    catalogue=CATALOGUE,
    documentation="docs",
)
