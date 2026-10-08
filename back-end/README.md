# Backend: første lokale etappe

Backend utvikles i Python med FastAPI og uv. Første leveranse er et lokalt grunnlag for opptaksmetadata og WAV-validering, med Entra-autentisering og en separat Key Vault-klient. [Hovedplanen](../plan-predmaint.md) beskriver den senere Azure-feltpiloten og sikkerhetsportene.

Kildekoden ligger i `src/predmaint_backend/`, og syntetiske tester ligger i `tests/`. Denne etappen bruker Python 3.14, fastsatt i `.python-version` og `pyproject.toml`.

## Omfang og sikkerhetsgrenser

Bruk bare syntetiske, ufølsomme opptak. Lokal SQLite og lokal fillagring er ikke den planlagte produksjonsarkitekturen i Azure. De innebærer ingen garanti om kryptering eller sikker backup; mappen må ligge på en beskyttet utviklingsmaskin, uten ukontrollert synkronisering eller deling.

Opptaksendepunktene skal være beskyttet også lokalt. Manglende autentiseringskonfigurasjon gir en tydelig feil, aldri en anonym fallback. API-tester kan injisere syntetiske identiteter i testprosessen; dette er ikke en kjøremodus eller et alternativ for publisert server.

Det opprettes ingen Azure-ressurser og gjøres ingen ekte Foundry-analyse i første leveranse. Ingen godkjent modell er valgt; analyse må derfor være eksplisitt utilgjengelig, ikke et oppdiktet feilfunn.

## Python-miljø

`pyproject.toml` og `uv.lock` versjoneres. `.venv/`, `.env` og `local-data/` holdes utenfor Git. Kjør fra denne katalogen:

```bash
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Start fra `back-end/`:

```bash
uv run --locked uvicorn predmaint_backend.app:create_app --factory \
  --host 127.0.0.1 --port 8000
```

Liveness finnes på `http://127.0.0.1:8000/health/live`; OpenAPI-/Swagger-dokumentasjonen finnes på `/docs`. Liveness sier bare at prosessen svarer, ikke at Entra, Azure eller modellen er klar. Uten autentiseringskonfigurasjon skal beskyttede endepunkter gi HTTP 503.

Utviklingsserveren bindes kun til `127.0.0.1`. Ikke bruk `--host 0.0.0.0` eller åpne den for iPhone/nettverk før autentisering, TLS, tilgang og databehandling er avklart. Lokal HTTP er kun for loopback-testing.

### Verifikasjonsstatus

172 syntetiske tester består, sammen med Ruff-/formatkontroll og låsefilkontroll. En midlertidig loopback-server er prøvd med liveness, OpenAPI, avvist tilgang uten autentiseringskonfigurasjon og avvist for stor metadata-body. Serveren ble stoppet etter kontrollen.

Testene bruker syntetiske JWT-er, lokale filer og fake Azure-klienter. Ingen ekte Entra-tokenutstedelse, vault, Private Endpoint, Azure-database, kø eller Foundry-modell er verifisert. TestClient gir en upstream deprecation-advarsel om HTTPX; den påvirker ikke resultatene, men skal følges opp ved oppgradering av teststakken.

## Entra-autentisering

Sett konfigurasjonen eksplisitt i prosessmiljøet:

```bash
export PREDMAINT_ENTRA_TENANT_ID='<organisasjonens tenant-UUID>'
export PREDMAINT_ENTRA_AUDIENCE='<API-ets eksakte audience>'
```

Dette er identifikatorer, ikke klienthemmeligheter. Serveren leser ikke `.env` automatisk. Bruk et gyldig Entra v2 access token for dette API-et i `Authorization: Bearer …`; ikke et ID-token. Tokenet må ha korrekt tenant, issuer, audience, gyldighet og brukerens `oid`. Rollen `operator` kreves for registrering, opplasting og fullføring. Lesing av opptak begrenses til den samme brukeren i samme tenant; bred fagperson-/administratorflyt er ikke implementert ennå.

Signaturen verifiseres med RS256 og Microsofts tenantbundne signeringsnøkler, med avgrenset cache og timeout. Ugyldig token gir 401, manglende skriverolle gir 403, og utilgjengelige signeringsnøkler gir 503. Entra-appregistrering, tildeling av app-roller og faktisk tokenutstedelse må konfigureres i deres tenant; dette opprettes ikke av koden.

Det finnes ingen anonym demoidentitet eller miljøvariabel som skrur av autentiseringen. API-tester bruker FastAPIs dependency override inne i testprosessen.

## Første API-kontrakt

| Endepunkt | Funksjon |
|---|---|
| `GET /health/live` | Offentlig liveness uten sensitive innstillinger |
| `POST /v1/recordings` | Registrere syntetiske metadata idempotent |
| `GET /v1/recordings?limit=20&offset=0` | Eier-/tenantavgrenset liste, maks 100 per side |
| `GET /v1/recordings/{id}` | Detaljer for eget opptak |
| `PUT /v1/recordings/{id}/audio` | Direkte WAV-body, ikke multipart |
| `POST /v1/recordings/{id}/complete` | Bekrefte validert lyd og returnere utilgjengelig analyse |

Registrering inneholder:

```json
{
  "client_recording_id": "11111111-1111-4111-8111-111111111111",
  "synthetic": true,
  "captured_at": "2026-10-08T12:00:00Z",
  "site_id": "synthetic-test-site",
  "track": "synthetic-track",
  "direction": "synthetic-direction",
  "train_number": null,
  "sha256": "<64 lowercase hexadecimal characters calculated from the WAV file>",
  "size_bytes": 96044,
  "frame_count": 48000,
  "sample_rate": 48000,
  "channels": 1,
  "sample_width_bits": 16
}
```

Eksemplet viser strukturen; SHA256-plassholderen må erstattes, og størrelse/frame count må beregnes fra den faktiske syntetiske filen. `synthetic: true` er et eksplisitt bruksforbehold, ikke et teknisk bevis på at innholdet er ufølsomt.

Metadata-body begrenses til 64 KiB før JSON-deserialisering, også uten `Content-Length`. Samme klient-ID, eier og identiske metadata gir samme server-ID. Endrede metadata med samme klient-ID gir 409. Server-ID-en brukes i de videre URL-ene. `PUT` krever `audio/wav`, `audio/x-wav` eller `application/octet-stream`, og mottar maksimalt 12 000 000 bytes. SHA256, størrelse, WAV-container, format og faktiske frames kontrolleres. Bare PCM 48 kHz, mono, 16 bit og varighet over 0 til og med 120 sekunder godtas.

Akseptert lyd overskrives ikke: gjentatt `PUT` gir 409. Etter usikkert opplastingsresultat må klienten hente status før den fortsetter. Fullføring kan gjentas, men krever at lydintegriteten fortsatt kan bekreftes. Statusene er `registered`, `uploaded` og `completed`; fullført lokal mottaksflyt er ikke det samme som fullført modellanalyse.

Et fullført opptak får `analysis_status: "unavailable"` med eksplisitt begrunnelse om manglende godkjent modell. Ingen køjobb, Foundry-kall eller feilprediksjon gjøres. Feil returneres som `{"error": {"code": "...", "message": "..."}}` uten token-, nøkkel- eller filinnhold.

Denne direkte lokale opplastingskontrakten er en avgrensning fra hovedplanens Azure/SAS-flyt. Målesteder, lydnedlasting, faglige vurderinger, sletting og reanalyse er ikke implementert i denne etappen.

## Azure Key Vault

Key Vault inngår fra første Azure-backendetappe, men metadata- og lydvalidering som ikke trenger hemmeligheter skal ikke kreve en kunstig tjenestenøkkel. Bruk Managed Identity i Azure; eksplisitt godkjent utvikleridentitet kan brukes mot utviklingsvault. Ingen automatisk produksjonsfallback til lokale nøkler.

Separate vaults per miljø, Private Endpoint, privat DNS, RBAC, soft delete, purge protection, rotasjon og tilgangslogging er obligatoriske utrullingskrav. En Python-SDK-klient etablerer ikke disse kontrollene alene. De skal senere etableres som infrastruktur som kode og verifiseres før feltbruk.

iPhone-appen får aldri backendhemmeligheter eller direkte vault-tilgang. Azure-/Foundry-nøkler skal ikke finnes i Git, app, logger, testdata eller containerimages.

SDK-grensesnittet ligger i `azure_security.py`:

```python
from predmaint_backend.azure_security import KeyVaultSecretProvider, VaultConfiguration

configuration = VaultConfiguration.from_environment()
with KeyVaultSecretProvider(configuration) as provider:
    secret = provider.get_secret("example-secret-name")
    # Bruk verdien i en nødvendig serveroperasjon; ikke skriv den til logger.
```

Dette er et separat integrasjonsgrensesnitt, ikke en HTTP-rute for å hente hemmeligheter. Metadata-/WAV-API-et kaller ikke vaulten, fordi disse operasjonene ikke trenger en hemmelighet.

Konfigurasjonen krever eksplisitt `PREDMAINT_AZURE_MODE` (`azure` eller `development`) og `PREDMAINT_ENVIRONMENT` (`development`, `test` eller `production`). Vault-URL leses fra `PREDMAINT_KEY_VAULT_URL_DEVELOPMENT`, `_TEST` eller `_PRODUCTION` etter valgt miljø, og må være en kanonisk HTTPS-URL som `https://example-dev-vault.vault.azure.net`. I Azure kan `PREDMAINT_MANAGED_IDENTITY_CLIENT_ID` angi en user-assigned identitets UUID; ellers brukes system-assigned identitet.

Development-modus bruker eksplisitt Azure CLI-identitet og tillater kun development-vaultkonfigurasjon. Azure-modus bruker Managed Identity uten fallback til CLI eller lokale nøkler. Det lokale API-et nekter å starte som cloud-/production-tjeneste; SDK-grensesnittet kan testes separat. Verken miljønavn eller URL-validering beviser at vaulten er korrekt geografisk plassert eller rettighetsavgrenset — det må kontrolleres ved utrulling.

Feil i konfigurasjon, tilgang, tomme hemmeligheter og gjenkjente SDK-/transportfeil gir eksplisitte feil uten hemmelighetsverdier. Klient og credential lukkes etter bruk.

## Før felt- eller produksjonsbruk

Første lokale grunnlag erstatter ikke den komplette API-/arbeiderflyten i hovedplanen. Produksjon krever blant annet Azure-lagring, database, kø/outbox, godkjent modellregion, objekttilgang, sletting, revisjonsspor, grenser og alarmer, TLS/nettverkskontroller, hendelsesrutiner og sikkerhetsgjennomgang.

Lovlig databehandling, feltprosedyrer og bekreftede feil-/vedlikeholdsdata kan ikke erstattes av tester eller en lokal demo.

Den lokale fil-/databaseflyten er ikke en distribuert transaksjon eller transactional outbox. Ved prosess-/maskinkrasj kan en lydfil bli stående uten samsvarende databaseoppdatering. En slik konflikt skal gi synlig feil, ikke automatisk overskriving eller sletting; inspiser syntetiske utviklingsdata manuelt. Automatisk krasjgjenoppretting og produksjonskvoter er videre arbeid.
