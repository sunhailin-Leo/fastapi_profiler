import time
import codecs
from typing import Optional
from logging import getLogger

from pyinstrument import Profiler
from pyinstrument.renderers import HTMLRenderer

from starlette.routing import Router
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = getLogger("profiler")


class PyInstrumentProfilerMiddleware:
    def __init__(
        self, app: ASGIApp,
        *,
        server_app: Optional[Router] = None,
        profiler_interval: float = 0.0001,
        profiler_output_type: str = "text",
        is_print_each_request: bool = True,
        **profiler_kwargs
    ):
        self.app = app
        self._profiler = Profiler(interval=profiler_interval)

        self._server_app = server_app
        self._output_type = profiler_output_type
        self._print_each_request = is_print_each_request
        self._profiler_kwargs: dict = profiler_kwargs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # register an event handler for profiler stop
        if self._server_app is not None:
            self._server_app.add_event_handler("shutdown", self.get_profiler_result)

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        self._profiler.start()

        request = Request(scope, receive=receive)
        method = request.method
        path = request.url.path
        begin = time.perf_counter()

        # Default status code used when the application does not return a valid response
        # or an unhandled exception occurs.
        status_code = 500

        async def wrapped_send(message: Message) -> None:
            if message['type'] == 'http.response.start':
                nonlocal status_code
                status_code = message['status']
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            if scope["type"] == "http":
                self._profiler.stop()
                end = time.perf_counter()
                if self._print_each_request:
                    print(f"Method: {method}, "
                          f"Path: {path}, "
                          f"Duration: {end - begin}, "
                          f"Status: {status_code}")
                    print(self._profiler.output_text(**self._profiler_kwargs))

    async def get_profiler_result(self):
        if self._output_type == "text":
            print(self._profiler.output_text(**self._profiler_kwargs))
        elif self._output_type == "html":
            html_name = self._profiler_kwargs.get("html_file_name")
            if html_name is None:
                html_name = "fastapi-profiler.html"

            """
             There are some problems with the args -- output_filename.
             You can check the
                class
                    'from pyinstrument.renderers import HTMLRenderer'
                method
                    'open_in_browser'
             the argument 'output_filename' will become the URL like 'file://xxxx',
             but that code have some bugs on it.

             So on my middleware, the args 'html_file_name'
             I suggest use None to instead, or you can use the absolute path.

             HTMLRenderer().open_in_browser(
                session=self._profiler.last_session,
                output_filename=html_name,
             )

             At last, I rewrite the function to avoid the problem!
             By the way, the html file default save at the root path of your project.
            """
            html_code = HTMLRenderer().render(session=self._profiler.last_session)
            with codecs.open(html_name, "w", "utf-8") as f:
                f.write(html_code)
