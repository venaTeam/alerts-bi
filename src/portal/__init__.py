"""Read-only review portal (design section 7.10).

Internal readers see every team's published weekly reviews, follow them over time, and
inspect individual alerts. They cannot start runs, publish, record decisions or change data.

The package is laid out by responsibility:

* :mod:`~src.portal.app` - the application factory: GET-only routes, the network allowlist,
  security headers
* :mod:`~src.portal.queries` - everything it reads, only through the ``portal_*`` views
* :mod:`~src.portal.pages` - the server-rendered HTML
* :mod:`~src.portal.explain` - plain-language reasons, next steps and decision questions
* :mod:`~src.portal.charts` - the weekly history charts, as inline SVG
* :mod:`~src.portal.assets` - the one stylesheet
* :mod:`~src.portal.server` - checking the credential, then running it under uvicorn

It deliberately imports nothing from :mod:`src.api`, :mod:`src.run`, :mod:`src.es`,
:mod:`src.llm` or :mod:`src.review`: there is no path from a reader's request to the run
pipeline, the unauthenticated run endpoint, Elasticsearch, the model, or an operator write.
"""
