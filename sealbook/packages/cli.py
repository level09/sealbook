"""`stk demo-cert`: a throwaway signing certificate so a development server can
build packages. Development and testing only; a real server signs with a
certificate from an authority on the C2PA trust list (docs/operations.md)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
from quart.cli import ScriptInfo
from stk.cli.base import console

DEMO = "Sealbook demo certificate, not issued by a trusted authority"


def make_signing_cert(
    directory: Path, days: int = 30, org: str = DEMO
) -> tuple[str, str]:
    """A throwaway ES256 certificate authority and signing certificate, in the
    shape c2patool accepts: cert.pem holds the signer then the authority, key.pem
    the signer's PKCS#8 key. Returns their paths."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.now(UTC)

    def name(cn):
        return x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, cn),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
            ]
        )

    def builder(subject, issuer, key):
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=days))
        )

    ca, leaf = (
        ec.generate_private_key(ec.SECP256R1()),
        ec.generate_private_key(ec.SECP256R1()),
    )
    ca_cert = (
        builder(name("Demo authority"), name("Demo authority"), ca)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            True,
        )
        .sign(ca, hashes.SHA256())
    )
    leaf_cert = (
        builder(name("Demo package signer"), ca_cert.subject, leaf)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False),
            True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()), False
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf.public_key()), False
        )
        .sign(ca, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = directory / "cert.pem", directory / "key.pem"
    cert.write_bytes(
        leaf_cert.public_bytes(serialization.Encoding.PEM)
        + ca_cert.public_bytes(serialization.Encoding.PEM)
    )
    key.write_bytes(
        leaf.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(key, 0o600)
    return str(cert), str(key)


@click.command("demo-cert")
@click.option("--days", type=click.IntRange(min=1), default=365, show_default=True)
@click.option("--force", is_flag=True, help="Replace a demo certificate already made.")
def demo_cert(days, force):
    """Write a demo package signing certificate into instance/ (dev only)."""
    app = click.get_current_context().ensure_object(ScriptInfo).load_app()
    if app.config.get("STK_ENV") not in ("development", "testing"):
        raise click.ClickException(
            "demo-cert writes a throwaway signing certificate; development only"
        )
    directory = Path(app.config["PROJECT_ROOT"]) / "instance" / "demo-cert"
    cert, key = directory / "cert.pem", directory / "key.pem"
    if force or not (cert.exists() and key.exists()):
        make_signing_cert(directory, days)
        console.print(f"Wrote a demo signing certificate, valid {days} days.")
    else:
        console.print("A demo signing certificate is already there (--force replaces).")
    console.print("Add to .env, then restart the server:")
    # Plain lines, never wrapped, so they paste into .env as they are.
    click.echo(f"C2PA_SIGN_CERT={cert}")
    click.echo(f"C2PA_SIGN_KEY={key}")
