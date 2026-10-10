# Security Policy

Metric Mirage is a local, single-user research tool. It does not provide authentication, authorization, or isolation between users. Keep the Streamlit server bound to a trusted local interface and do not expose it as a public multi-user service without a separate security design.

## Reporting a Vulnerability

If this repository has GitHub private vulnerability reporting enabled, use **Security → Report a vulnerability** to contact the maintainers privately. Availability depends on the repository's settings; this document does not claim that reporting is already enabled.

If that option is unavailable, open a minimal issue asking the maintainer to establish a private reporting channel. Do not include exploit details, private PDFs, registry contents, credentials, personal information, or sensitive evidence in the public request. Share details only through a private channel agreed with the maintainer. No dedicated security email address or response-time commitment is currently published.

A useful private report includes the affected version or revision, environment, reproduction steps with synthetic inputs, expected and observed behavior, and the likely impact. Do not upload real source reports when a synthetic example can reproduce the issue.

## Data and Deployment Boundaries

- Use only reports you have the right to process. The default dashboard storage directory `.metric_mirage/` contains local PDFs, registry metadata, definitions, and evidence; keep these out of public Git history.
- Uploaded PDF filenames do not determine storage paths. The dashboard validates PDF format characteristics, enforces upload limits, and uses generated names and non-overwriting publication.
- Exported JSON, snapshots, and custom data directories can contain sensitive company or source information. Git ignore rules are a convenience, not access control; inspect files before publishing.
- PDFs remain untrusted inputs to a parser. Format checks do not certify a document as harmless, and unusually complex files may require substantial memory or time.
- SHA256 identifies file content and checks consistency. It is not a digital signature, publisher-authenticity guarantee, or independent financial verification.
- Historical evidence snapshots do not verify the current contents of the original PDF when viewed or compared.

Security fixes should preserve existing user data, safe file handling, and the local single-user workflow. Keep dependencies updated and review the release notes before processing unfamiliar files.
