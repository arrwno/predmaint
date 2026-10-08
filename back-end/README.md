# Backend

Denne katalogen er reservert for predmaint-backend i Python, med uv for virtuelt miljø og avhengigheter. Backend skal senere kjøre i Azure.

Se [hovedplanen](../plan-predmaint.md) for API, lagring, analyse, sikkerhet og Foundry-beslutningsport.

Backend planlegges med høy cyberrisiko som premiss. Azure Key Vault etableres fra første backendetappe, med separate miljøer, Private Endpoint, RBAC, Managed Identity, rotasjon og tilgangslogging. Identitetsbasert tjenestetilgang foretrekkes; iPhone-appen får aldri vault-tilgang eller backendhemmeligheter. Se hovedplanens sikkerhetsporter før implementering og feltbruk.

Ingen backend, Python-miljø, avhengigheter eller Azure-ressurser opprettes som del av den første lokale iPhone-appen. Ved senere implementering versjoneres `pyproject.toml` og `uv.lock`, mens `.venv/`, hemmeligheter og lokale lyddata holdes utenfor Git.

Bruk `local-data/` for lokale opptak eller datasett på utviklingsmaskinen; katalogen ignoreres av Git.
