"""Explicit input selection and muxing. Indices are independent per input type."""

import argparse
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from reader import Reader, langcodes, language_display_name, normalize_track_language


@dataclass
class InputFile:
    path: str
    kind: str
    index: int
    stream: int = 0
    language: str | None = None
    offset: float = 0.0


@dataclass
class Join:
    references: list[str]
    language: str | None = None


@dataclass
class Plan:
    video: InputFile
    audio: list[InputFile] = field(default_factory=list)
    subtitles: list[InputFile] = field(default_factory=list)
    language: str | None = None

    @property
    def inputs(self):
        return [self.video, *self.audio, *self.subtitles]


def hls_input(source, kind, stream=0):
    """Use the selected source directly, retaining its container timeline."""
    if not isinstance(source, InputFile):
        source = InputFile(str(source), kind, 0, stream=stream)
    if source.kind != kind:
        raise ValueError(f"Expected a {kind} input, got {source.kind}")
    options = ["-copyts", "-start_at_zero"]
    if source.offset:
        options += ["-itsoffset", str(source.offset)]
    options += ["-i", str(Path(source.path).resolve())]
    return options, f"0:{kind}:{source.stream}"


class AddInput(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        items = list(getattr(namespace, self.dest, None) or [])
        kind = {"videos": "v", "audios": "a", "subtitles": "s"}[self.dest]
        items.append(InputFile(values, kind, len(items)))
        setattr(namespace, self.dest, items)


class SetInputOption(argparse.Action):
    def __init__(self, *args, collection, field_name, **kwargs):
        self.collection = collection
        self.field_name = field_name
        super().__init__(*args, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        items = getattr(namespace, self.collection, None) or []
        if not items:
            parser.error(f"{option_string} must follow a {self.collection[:-1]} input")
        setattr(items[-1], self.field_name, values)


class AddJoin(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        joins = list(getattr(namespace, self.dest, None) or [])
        joins.append(Join(values))
        setattr(namespace, self.dest, joins)


class SetJoinLanguage(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        joins = getattr(namespace, "joins", None) or []
        if not joins:
            parser.error(f"{option_string} must follow -join or --join")
        if joins[-1].language is not None:
            parser.error("Each join accepts only one language label")
        joins[-1].language = values


def nonnegative_integer(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a non-negative integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("Expected a non-negative integer")
    return number


def finite_seconds(value):
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a finite offset in seconds") from exc
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("Expected a finite offset in seconds")
    return number


def explicit_language(value):
    """Resolve language names; preserve rare, regional and private-use tags."""
    text = str(value).strip()
    tag = normalize_track_language(text)
    if not text:
        raise ValueError("A language label cannot be empty")
    if langcodes is not None:
        if tag != "und" and langcodes.tag_is_valid(tag):
            return tag
        if text.lower() in ("und", "zxx"):
            return text.lower()
        try:
            return str(langcodes.find(text))
        except (LookupError, ValueError, ModuleNotFoundError):
            pass
    if tag != "und" and (len(text) in (2, 3) or "-" in text or "_" in text):
        return tag
    if text.lower() == "und":
        return "und"
    raise ValueError(f"Unknown language name {value!r}; use a code such as ja, yue, pt-BR or x-custom. Install langcodes[data] for names.")


def add_input_arguments(parser):
    for short, name, collection in (("v", "video", "videos"), ("a", "audio", "audios"), ("s", "subtitle", "subtitles")):
        parser.add_argument(f"-{short}", f"--{name}", dest=collection, action=AddInput, metavar="FILE",
                            help=f"Add a {name} input; repeat for more files. Selects stream 0 by default.")
        parser.add_argument(f"--{name}-stream", action=SetInputOption, collection=collection,
                            field_name="stream", type=nonnegative_integer, metavar="INDEX",
                            help=f"Select a stream within the preceding -{short} file.")
        if short != "v":
            parser.add_argument(f"-{short}l", f"--{name}-lang", action=SetInputOption, collection=collection,
                                field_name="language", metavar="LANGUAGE", help=f"Language name/tag for the preceding -{short}.")
            parser.add_argument(f"--{name}-offset", action=SetInputOption, collection=collection,
                                field_name="offset", type=finite_seconds, metavar="SECONDS",
                                help=f"Shift the preceding -{short} input; positive values delay it.")
    parser.add_argument("-join", "--join", dest="joins", action=AddJoin, nargs="+", metavar="INDEX:TYPE",
                        help="Select one video and zero or more audio/subtitle inputs, e.g. 0:v 0:a 1:a 0:s.")
    parser.add_argument("-jl", "--join-language", "--join-lang", action=SetJoinLanguage, metavar="LANGUAGE",
                        help="Directory language/version for the preceding join.")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and print JSON without writing media.")


def build_plans(args, normalize_directory):
    collections = {"v": args.videos or [], "a": args.audios or [], "s": args.subtitles or []}
    if not any(collections.values()):
        if args.joins:
            raise ValueError("-join requires explicit -v/-a/-s inputs")
        return None
    if args.input:
        raise ValueError("Use either --input for automatic discovery or -v/-a/-s for explicit selection")
    if not collections["v"]:
        raise ValueError("Explicit input mode requires at least one -v video file")
    for items in collections.values():
        for item in items:
            if not Path(item.path).is_file():
                raise ValueError(f"Input {item.index}:{item.kind} is not a file: {item.path}")
            if item.language is not None:
                item.language = explicit_language(item.language)
    if not args.joins:
        if len(collections["v"]) != 1:
            raise ValueError("Multiple -v inputs require explicit -join selections and distinct -jl labels")
        return [Plan(collections["v"][0], collections["a"], collections["s"], args.language)]
    plans = []
    used_languages = set()
    for join in args.joins:
        selected = {"v": [], "a": [], "s": []}
        seen = set()
        for reference in join.references:
            match = re.fullmatch(r"(\d+):([vas])", reference)
            if not match:
                raise ValueError(f"Invalid join reference {reference!r}; use 0:v, 0:a or 0:s")
            index, kind = int(match[1]), match[2]
            if index >= len(collections[kind]):
                raise ValueError(f"Join reference {reference} does not exist; {len(collections[kind])} {kind} input(s) supplied")
            if (index, kind) in seen:
                raise ValueError(f"Duplicate join reference {reference}")
            seen.add((index, kind))
            selected[kind].append(collections[kind][index])
        if len(selected["v"]) != 1:
            raise ValueError("Each join must contain exactly one video reference")
        language = normalize_directory(join.language if join.language is not None else args.language)
        if len(args.joins) > 1 and language is None:
            raise ValueError("Multiple joins require distinct -jl language/version labels")
        if language in used_languages:
            raise ValueError(f"Two joins would overwrite the same language folder: {language}")
        used_languages.add(language)
        # Share subtitles by default; explicit subtitle references select a subset.
        plans.append(Plan(selected["v"][0], selected["a"], selected["s"] or collections["s"], language))
    return plans


def inspect_plans(plans, args):
    """Probe all selected sources with MediaInfo before starting an encode."""
    reports = {}
    readers = []
    for plan in plans:
        chosen = []
        for item in plan.inputs:
            path = str(Path(item.path).resolve())
            if path not in reports:
                reports[path] = Reader(path, True, True, args.mediainfo_path).probe_media()
            kind = {"v": "Video", "a": "Audio", "s": "Text"}[item.kind]
            tracks = [track for track in reports[path].get("media", {}).get("track", []) if track.get("@type") == kind]
            if item.stream >= len(tracks):
                raise ValueError(f"{item.index}:{item.kind} ({item.path}) has no {kind} stream {item.stream}; found {len(tracks)}")
            chosen.append(tracks[item.stream])
        reader = Reader(plan.video.path, True, True, args.mediainfo_path)
        reader.read(args, {"media": {"track": chosen}})
        for items, tracks in ((plan.audio, reader.audio_tracks), (plan.subtitles, reader.subtitle_tracks)):
            for item, track in zip(items, tracks):
                if item.language is not None:
                    track.update(language=item.language, language_short=item.language.split("-")[0],
                                 language_region=item.language, language_name=language_display_name(item.language),
                                 metadata_source="CLI (technical metadata: MediaInfo)")
                track.update(source_file=Path(item.path).name, source_input=item.index,
                             source_stream=item.stream, source_offset_seconds=item.offset)
        readers.append(reader)
    return readers


def mux_command(plan, reader, output_path, nice=None):
    command = ["ffmpeg", "-nostdin", "-y", "-copyts", "-start_at_zero"]
    sources = {}
    maps = []
    for item in plan.inputs:
        key = (str(Path(item.path).resolve()), item.offset)
        if key not in sources:
            sources[key] = len(sources)
            if item.offset:
                command += ["-itsoffset", str(item.offset)]
            command += ["-i", key[0]]
        maps += ["-map", f"{sources[key]}:{item.kind}:{item.stream}"]
    command += maps + ["-map_metadata", "-1", "-map_chapters", "0", "-c", "copy"]
    for kind, tracks in (("a", reader.audio_tracks), ("s", reader.subtitle_tracks)):
        default = next((i for i, track in enumerate(tracks) if track.get("default")), 0)
        for index, track in enumerate(tracks):
            command += [f"-metadata:s:{kind}:{index}", f"language={track['language']}"]
            if track.get("title"):
                command += [f"-metadata:s:{kind}:{index}", f"title={track['title']}"]
            flags = ["default"] if index == default else []
            for flag, ffmpeg_flag in (("forced", "forced"), ("hearing_impaired", "hearing_impaired"),
                                      ("visual_impaired", "visual_impaired"), ("commentary", "comment")):
                if track.get(flag):
                    flags.append(ffmpeg_flag)
            command += [f"-disposition:{kind}:{index}", "+".join(flags) or "0"]
    command += ["-avoid_negative_ts", "disabled", "-f", "matroska", str(output_path)]
    return ["nice", "-n", str(nice), *command] if nice is not None else command


def mux_original(plan, reader, output_path, nice=None):
    """Replace the download only after a successful mux, without a second copy."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".lapis-join-", suffix=".mkv", dir=output_path.parent)
    os.close(descriptor)
    try:
        subprocess.run(mux_command(plan, reader, temporary, nice), check=True)
        os.chmod(temporary, 0o644)
        os.replace(temporary, output_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
