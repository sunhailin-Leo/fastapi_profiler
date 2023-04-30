import os
import time
import codecs
import cProfile
from io import StringIO
from typing import Optional
from logging import getLogger

from pyinstrument import Profiler
from pyinstrument.renderers import HTMLRenderer

from starlette.routing import Router
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = getLogger("profiler")


class PyInstrumentProfilerMiddleware:
    DEFAULT_HTML_FILENAME = "./fastapi-profiler.html"
    DEFAULT_PROF_FILENAME = "./fastapi-profiler.prof"

    def __init__(
        self,
        app: ASGIApp,
        *,
        server_app: Optional[Router] = None,
        profiler_interval: float = 0.0001,
        profiler_output_type: str = "text",
        is_print_each_request: bool = True,
        async_mode: str = "enabled",
        html_file_name: Optional[str] = None,
        prof_file_name: Optional[str] = None,
        open_in_browser: bool = False,
        **profiler_kwargs,
    ):
        self.app = app
        self._output_type = profiler_output_type
        self._print_each_request = is_print_each_request
        self._html_file_name: Optional[str] = html_file_name
        self._prof_file_name: Optional[str] = prof_file_name
        self._open_in_browser: bool = open_in_browser
        self._profiler_kwargs: dict = profiler_kwargs

        if profiler_output_type == "html" and server_app is None:
            raise RuntimeError(
                "If profiler_output_type=html, must provide server_app argument "
                "to set shutdown event handler to output profile."
            )

        if profiler_output_type == "prof":
            self._profiler = cProfile.Profile()
            self._start_profiler = self._profiler.enable
            self._stop_profiler = self._profiler.disable
        else:
            self._profiler = Profiler(interval=profiler_interval, async_mode=async_mode)
            self._start_profiler = self._profiler.start
            self._stop_profiler = self._profiler.stop

        # register an event handler for profiler stop
        if server_app is not None:
            server_app.add_event_handler("shutdown", self.get_profiler_result)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        self._start_profiler()

        request = Request(scope, receive=receive)
        method = request.method
        path = request.url.path
        begin = time.perf_counter()

        # Default status code used when the application does not return a valid response
        # or an unhandled exception occurs.
        status_code = 500

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                nonlocal status_code
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            if scope["type"] == "http":
                self._stop_profiler()
                end = time.perf_counter()
                if self._print_each_request:
                    print(
                        f"Method: {method}, "
                        f"Path: {path}, "
                        f"Duration: {end - begin}, "
                        f"Status: {status_code}"
                    )

                    if self._output_type == "prof":
                        s = StringIO()
                        self._profiler.print_stats(stream=s)
                        print(s.getvalue())
                    else:
                        print(self._profiler.output_text(**self._profiler_kwargs))

    async def get_profiler_result(self):
        if self._output_type == "text":
            logger.info("Compiling and printing final profile")
            print(self._profiler.output_text(**self._profiler_kwargs))
        elif self._output_type == "html":
            html_file_name = self.DEFAULT_HTML_FILENAME
            if self._html_file_name is not None:
                html_file_name = self._html_file_name

            logger.info(
                "Compiling and dumping final profile to %r - this may take some time",
                html_file_name,
            )

            renderer = HTMLRenderer()
            if self._open_in_browser:
                renderer.open_in_browser(
                    session=self._profiler.last_session,
                    output_filename=os.path.abspath(html_file_name),
                )
            else:
                html_code = renderer.render(session=self._profiler.last_session)
                with codecs.open(html_file_name, "w", "utf-8") as f:
                    f.write(html_code)

            logger.info("Done writing profile to %r", html_file_name)
        elif self._output_type == "prof":
            prof_file_name = self.DEFAULT_PROF_FILENAME
            if self._prof_file_name is not None:
                prof_file_name = self._prof_file_name

            logger.info(
                "Compiling and dumping final profile to %r - this may take some time",
                prof_file_name,
            )

            self._profiler.dump_stats(prof_file_name)
            logger.info("Done writing profile to %r", prof_file_name)
