"""Can this Twisted + pyOpenSSL pair actually complete a TLS handshake?

Importing pyOpenSSL is not the same question. The campus HTTPS port spent a
release serving nothing while looking perfectly healthy: daphne logged
"Listening on TCP address 0.0.0.0:8443", the certificate was valid, the
endpoint string parsed - and every single handshake was dropped, because
pyOpenSSL 26 refuses to mutate an SSL.Context once it has created a Connection
while Twisted 24.11 sets the ALPN callback on every new connection.

Nothing surfaced that. Guards on other devices simply found the camera did not
work, since a browser only allows it on a secure page and the secure page had
quietly stopped answering.

So this checks the behaviour rather than the version: it does exactly what
Twisted does per connection - use the context, then set the ALPN callback on
it - and reports whether the pair tolerates it. A version test would have to be
rewritten the moment either project changes its bounds.

Prints 'ok' and exits 0 when HTTPS will work, or a one-line reason and exits 1.
run-campus.ps1 turns the HTTPS port off when this fails, so the UI does not
offer a secure link that leads nowhere.
"""
import sys


def main():
    try:
        from OpenSSL import SSL
        from twisted.internet import ssl as twisted_ssl
        from twisted.internet._sslverify import _setAcceptableProtocols
    except Exception as exc:                       # not installed at all
        print('pyOpenSSL/service_identity are missing (%s)' % exc)
        return 1

    try:
        context = twisted_ssl.CertificateOptions().getContext()
        # Twisted marks the context used by building a Connection from it...
        SSL.Connection(context)
        # ...and then sets the protocol negotiation callback on that same
        # context for every connection it accepts. This is the call that
        # raises on an incompatible pair.
        _setAcceptableProtocols(context, [b'http/1.1'])
    except Exception as exc:
        try:
            from OpenSSL.version import __version__ as pyopenssl_version
        except Exception:
            pyopenssl_version = '?'
        print('pyOpenSSL %s cannot serve TLS with this Twisted (%s): %s'
              % (pyopenssl_version, type(exc).__name__, exc))
        return 1

    print('ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
