"""Lossless JSON storage used by the consolidated experimental catalog."""
import zlib


def decode_json(value):
    return zlib.decompress(value).decode('utf-8') if isinstance(value, bytes) else value


def register_json_storage(connection):
    connection.create_function('experiment_json', 1, decode_json, deterministic=True)


def pack_json(value):
    return zlib.compress(value.encode('utf-8'))
