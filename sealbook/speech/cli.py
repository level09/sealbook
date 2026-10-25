"""Fetch the speech model once; workers never download."""

import click

from sealbook import speech
from sealbook.settings import Config


@click.command("speech-model")
def speech_model():
    """Download the local speech-to-text model weights and check they load."""
    click.echo(f"Fetching {Config.SPEECH_MODEL} into {Config.SPEECH_MODEL_DIR}")
    for key, value in speech.download().items():
        click.echo(f"{key}: {value}")
