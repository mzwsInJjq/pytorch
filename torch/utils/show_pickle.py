#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
import pickle
import pprint
import struct
import sys
import zipfile
from typing import Any, IO

__all__ = ["FakeObject", "FakeClass", "DumpUnpickler", "main"]

class FakeObject:
    def __init__(self, module: str, name: str, args: tuple[Any, ...]) -> None:
        self.module = module
        self.name = name
        self.args = args
        # NOTE: We don't distinguish between state never set and state set to None.
        self.state: Any = None

    def __repr__(self) -> str:
        state_str = "" if self.state is None else f"(state={self.state!r})"
        return f"{self.module}.{self.name}{self.args!r}{state_str}"

    def __setstate__(self, state: Any) -> None:
        self.state = state

    @staticmethod
    def pp_format(printer: Any, obj: FakeObject, stream: Any, indent: int, allowance: int, context: Any, level: int) -> None:
        if not obj.args and obj.state is None:
            stream.write(repr(obj))
            return
        if obj.state is None:
            stream.write(f"{obj.module}.{obj.name}")
            printer._format(obj.args, stream, indent + 1, allowance + 1, context, level)
            return
        if not obj.args:
            stream.write(f"{obj.module}.{obj.name}()(state=\n")
            indent += printer._indent_per_level
            stream.write(" " * indent)
            printer._format(obj.state, stream, indent, allowance + 1, context, level + 1)
            stream.write(")")
            return
        raise Exception("Need to implement")  # noqa: TRY002


class FakeClass:
    def __init__(self, module: str, name: str) -> None:
        self.module = module
        self.name = name
        self.__new__ = self.fake_new  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"{self.module}.{self.name}"

    def __call__(self, *args: Any) -> FakeObject:
        return FakeObject(self.module, self.name, args)

    def fake_new(self, *args: Any) -> FakeObject:
        return FakeObject(self.module, self.name, args[1:])


class DumpUnpickler(pickle._Unpickler):  # type: ignore[name-defined]
    def __init__(
            self,
            file: IO[bytes],
            *,
            catch_invalid_utf8: bool = False,
            **kwargs: Any) -> None:
        super().__init__(file, **kwargs)
        self.catch_invalid_utf8 = catch_invalid_utf8

    def find_class(self, module: str, name: str) -> FakeClass:
        return FakeClass(module, name)

    def persistent_load(self, pid: Any) -> FakeObject:
        return FakeObject("pers", "obj", (pid,))

    dispatch = dict(pickle._Unpickler.dispatch)  # type: ignore[attr-defined]

    # Custom objects in TorchScript are able to return invalid UTF-8 strings
    # from their pickle (__getstate__) functions.  Install a custom loader
    # for strings that catches the decode exception and replaces it with
    # a sentinel object.
    def load_binunicode(self) -> None:
        strlen, = struct.unpack("<I", self.read(4))  # type: ignore[attr-defined]
        if strlen > sys.maxsize:
            raise Exception("String too long.")  # noqa: TRY002
        str_bytes = self.read(strlen)  # type: ignore[attr-defined]
        obj: Any
        try:
            obj = str(str_bytes, "utf-8", "surrogatepass")
        except UnicodeDecodeError as exn:
            if not self.catch_invalid_utf8:
                raise
            obj = FakeObject("builtin", "UnicodeDecodeError", (str(exn),))
        self.append(obj)  # type: ignore[attr-defined]
    dispatch[pickle.BINUNICODE[0]] = load_binunicode  # type: ignore[assignment]

    @classmethod
    def dump(cls, in_stream: IO[bytes], out_stream: Any) -> Any:
        value = cls(in_stream).load()
        pprint.pprint(value, stream=out_stream)
        return value


def main(argv: list[str], output_stream: Any = None) -> int:
    if len(argv) != 2:
        # Don't spam stderr if not using stdout.
        if output_stream is not None:
            raise Exception("Pass argv of length 2.")  # noqa: TRY002
        sys.stderr.write("usage: show_pickle PICKLE_FILE\n")
        sys.stderr.write("  PICKLE_FILE can be any of:\n")
        sys.stderr.write("    path to a pickle file\n")
        sys.stderr.write("    file.zip@member.pkl\n")
        sys.stderr.write("    file.zip@*/pattern.*\n")
        sys.stderr.write("      (shell glob pattern for members)\n")
        sys.stderr.write("      (only first match will be shown)\n")
        return 2

    fname = argv[1]
    handle: IO[bytes]
    if "@" not in fname:
        with open(fname, "rb") as handle:
            DumpUnpickler.dump(handle, output_stream)
    else:
        zfname, mname = fname.split("@", 1)
        with zipfile.ZipFile(zfname) as zf:
            if "*" not in mname:
                with zf.open(mname) as handle:
                    DumpUnpickler.dump(handle, output_stream)
            else:
                found = False
                for info in zf.infolist():
                    if fnmatch.fnmatch(info.filename, mname):
                        with zf.open(info) as handle:
                            DumpUnpickler.dump(handle, output_stream)
                        found = True
                        break
                if not found:
                    raise Exception(f"Could not find member matching {mname} in {zfname}")  # noqa: TRY002
    return 0


if __name__ == "__main__":
    # This hack works on every version of Python I've tested.
    # I've tested on the following versions:
    #   3.7.4
    if True:
        pprint.PrettyPrinter._dispatch[FakeObject.__repr__] = FakeObject.pp_format  # type: ignore[attr-defined]

    sys.exit(main(sys.argv))
