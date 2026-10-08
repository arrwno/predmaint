# Plan: Repo-struktur og første lokale iPhone-app

## Dokumentstatus

Dette er den samlede planen som ble godkjent før repo-strukturen og den lokale app-planen ble publisert i commit `cb02c93`. Den bevares her som Git-versjonert planleggingsgrunnlag, ikke som en ny bestilling av implementering.

Dokumentasjonsleveransen nedenfor er gjennomført. [Den detaljerte app-planen](plan-iphone.md) er hovedkilden for videre frontend-planlegging, og [hovedplanen](../plan-predmaint.md) beskriver det langsiktige systemet. Beskrivelsen av nåtilstanden nedenfor gjelder tidspunktet før dokumentasjonsleveransen.

## Problem og tilnærming

Teamet kjenner Azure/backend, men er nybegynnere på iOS. Neste leveranse skal derfor dokumentere en enkel lokal opptaksapp og etablere tydelig frontend-/backend-separasjon. Ingen appkode, Xcode-prosjekt eller Python-miljø implementeres i dette steget.

Brukeren har bekreftet lokale opptak, opptaksliste og avspilling, med nybegynnerveiledning. Opptak skjer bare i forgrunnen, med maksimalt 120 sekunder og WAV-format. Azure, Foundry, innlogging, opplasting og bakgrunnsopptak er utenfor første læringsapp.

Den publiserte hovedplanen er `plan-predmaint.md`; denne skal fortsatt beskrive det langsiktige systemet. Den nye frontend-planen er en avgrenset første etappe, ikke en erstatning for systemdesignet. Brukeren vil ha Git-historikk for planutvikling.

## Undersøkt nåtilstand før dokumentasjonsleveransen

Repoet har `.gitignore`, `README.md`, `LICENSE` og `plan-predmaint.md`, men ingen kildekode eller frontend-/backend-mapper. De publiserte dokumentene er også på `main`. Arbeidsflaten var ren ved inspeksjon. Sesjonen er på `arrwno-akustisk-jernbanevedlikehold`; denne branchen brukes fortsatt, uten push direkte til `main` eller automatisk PR-opprettelse.

## Filer etter godkjenning

```text
/
├── README.md
├── plan-predmaint.md
├── .gitignore
├── front-end/
│   ├── README.md
│   └── plan-iphone.md
└── back-end/
    └── README.md
```

Git versjonerer ikke tomme mapper. Korte README-filer gir mappene et reelt formål og gjør dem synlige på GitHub, uten unødvendige `.gitkeep`-filer.

`front-end/README.md` peker til app-planen og forklarer at et senere Xcode-prosjekt skal ligge i `front-end/ios/`. `back-end/README.md` reserverer området for senere Python/uv-backend og peker til hovedplanen. Ingen `uv init`, avhengigheter eller Azure-ressurser opprettes nå.

Rotens README lenker til hovedplanen og begge delene. Hovedplanen får en kort beskrivelse av den lokale første etappen, den nye strukturen og lenke til app-planen. Tidligere formuleringer om at bare to dokumenter er bestilt, oppdateres slik at de ikke feilaktig begrenser denne nye leveransen. Behold tidligere systemkrav og sikkerhetsbegrensninger.

`.gitignore` får avgrensede regler for `/front-end/local-data/` og `/back-end/local-data/`; eksisterende Python-, hemmelighets- og Xcode-regler beholdes. Ikke ignorer Swift-kildekode, Xcode-prosjektfiler, delte schemes, `pyproject.toml` eller `uv.lock`.

## Innhold i front-end/plan-iphone.md

### Mål og avgrensning

Lag senere en native Swift/SwiftUI-app med AVFoundation. Den skal kunne ta opp, lagre, liste og spille av lyd lokalt uten nett. Appen skal ikke analysere lyd eller trekke konklusjoner om togets tilstand.

Foreslått minimum er iOS 17 for å bruke moderne mikrofontillatelses-API. Versjonen bekreftes mot teamets testtelefon og Xcode før implementering. Bruk ingen tredjepartsbiblioteker i første versjon.

### Første brukerflyt

1. Appen viser en enkel skjerm med «Start opptak» og liste over tidligere opptak.
2. Ved første start forklarer appen mikrofonbruk og ber om tillatelse.
3. Under opptak vises «Stopp», varighet og tydelig opptaksstatus. Avspilling er deaktivert.
4. Manuell stopp eller 120 sekunder avslutter og lagrer opptaket før det vises i listen.
5. En listeoppføring viser automatisk navn/tidspunkt og varighet. Brukeren kan starte og stoppe avspilling; bare én lydaktivitet kan være aktiv.
6. Lagrede opptak finnes fortsatt etter at appen avsluttes og åpnes igjen.

Avbryt og lagre et gyldig delopptak når appen går i bakgrunnen eller lydøkten avbrytes, hvis filen kan ferdigstilles. Vis årsaken og marker opptaket som ufullstendig. Ved feil skal appen ikke hevde at lyd er lagret; ingen stille sletting av tidligere opptak. Gjenoppta aldri opptak automatisk.

Ingen redigering, deling, eksport, avansert bølgeform, automatisk sletting eller feltmetadata i denne første versjonen. Dette er en lokal læringsapp, ikke en operativ feltpilot.

### Enkel teknisk utforming

Bruk `AVAudioRecorder` for opptak, `AVAudioPlayer` for avspilling og `AVAudioSession` for lydøkt. Bruk `AVAudioApplication.requestRecordPermission` ved iOS 17 eller nyere. `NSMicrophoneUsageDescription` må settes i targetens genererte Info.plist/build settings, med en forståelig norsk begrunnelse.

Bruk SwiftUI med én hovedskjerm, én liten modell for opptaksmetadata, en lokal opptaks-/avspillingskontroller og en enkel filbasert opptakslagring. Ikke innfør omfattende lagdeling, dependency injection-rammeverk eller serverkontrakter.

Planlagt Xcode-prosjekt legges under `front-end/ios/`. Dokumenter foreslåtte ansvar for appstart, skjerm, lydkontroller og lagring uten å opprette kildefiler nå.

Lagre WAV/PCM, mono, 48 kHz og 16 bit som målformat. Verifiser faktisk opptaksformat og innspillingsrute på fysisk iPhone; ikke anta at maskinvarens format alltid er det forespurte. Enheten er ikke en kalibrert lydtrykksmåler.

Lyd og et lite metadataregister lagres i appens private Application Support-område, ikke i caches, repoet eller en offentlig mappe. Bruk UUID i filnavn og automatisk dato i visningsnavn. Registeret inkluderer ID, relativ filreferanse, tidspunkt, faktisk varighet og eventuell avbruddsårsak. Skriv metadata atomisk og kontroller konsistens mellom register og lydfiler ved oppstart; avvik vises eksplisitt.

Bruk iOS Data Protection og ekskluder prototypeopptak fra backup. Beskriv at avinstallering kan slette alle lokale opptak, og at det ikke finnes skybackup eller eksport i denne versjonen. Ikke logg lydinnhold eller personopplysninger.

Tilstander er eksempelvis klar, ber om mikrofontilgang, tar opp, lagrer, spiller av og feil. De skal styre hvilke knapper som kan trykkes. Kontroller returverdier, delegates og feil fra lyd- og fil-API-ene; ikke oppdater UI til suksess før handlingen er bekreftet.

### Nybegynnerveiledning

Forklar rollene til Swift, SwiftUI, AVFoundation, Xcode, simulator, target, bundle identifier og signing.

Veiledningen skal beskrive:

- Installer Xcode på en kompatibel Mac og åpne Xcode for nødvendig førstegangsoppsett.
- Opprett senere et iOS App-prosjekt med Swift og SwiftUI i `front-end/ios/`; unngå et eget nestet Git-repo når veiviseren tilbyr det.
- Velg en unik bundle identifier og team under Signing & Capabilities. Apple Account/Personal Team kan brukes til lokal testing med Apples begrensninger; betalt medlemskap er ikke et ubetinget krav for første test på egen telefon.
- Koble til og godkjenn Macen fra iPhone. Aktiver Developer Mode når nødvendig og velg telefonen som run destination.
- Legg til mikrofonbegrunnelsen, bygg og kjør. Bruk simulatoren for UI, men fysisk iPhone for mikrofon, format, avbrudd og lokal lagring.
- Ikke aktiver Background Audio eller legg inn nettverks-/Azure-konfigurasjon for denne versjonen.

Eksakt Xcode-/iOS-kompatibilitet og signing-begrensninger kontrolleres ved implementering; ingen lokale verktøy antas installert uten sjekk.

### Feil og akseptansekriterier ved implementering

Manglende mikrofontillatelse gir forklaring og vei til Innstillinger, ikke en tom fil eller gjentatte systemprompter. Full disk, utilgjengelig mikrofon, mislykket lagring og avspilling gir tydelige feil. Tidligere opptak bevares.

Kontroller på fysisk iPhone at:

- Et opptak kan startes, stoppes, listes og spilles av uten nett.
- Opptakene finnes etter omstart av appen.
- Automatisk stopp håndhever 120 sekunder; lagret varighet kontrolleres.
- Ingen opptak eller avspilling overlapper, også ved raske knappetrykk.
- Nektet tillatelse og endring via Innstillinger håndteres.
- Bakgrunn, skjermlås og lydavbrudd gir kontrollert stopp og synlig status.
- Lagrings-/metadatafeil og ugyldige lydfiler håndteres uten falsk suksess.

Ved senere implementering brukes målrettede tester for metadata/lagring og tilstandsoverganger, Xcode-build og manuell lydtest på telefon. Dokumentasjonsleveransen nå krever ingen app-build.

## Todos for denne dokumentasjonsleveransen

1. Opprette `front-end/README.md`, `front-end/plan-iphone.md` og `back-end/README.md`.
2. Oppdatere rot-README, hovedplan og avgrensede lokale dataregler i `.gitignore`.
3. Kontrollere lenker, omfang, Git-diff og ignore-regler; committe og pushe de bestilte dokumentasjonsendringene til eksisterende feature-branch og verifisere fjerncommit.

Todo 2 avhenger av 1 for dokumentlenkene. Todo 3 avhenger av både 1 og 2. Appimplementering er fremtidig arbeid og krever ny bestilling; den gamle Azure-piloten er ikke en forutsetning for læringsappen.

## Forbehold

Ikke gjør opptak av andre personer uten riktig grunnlag. Første læringstest bør skje i et kontrollert miljø, ikke ved jernbanesporet. Feltbruk krever de godkjenningene og prosedyrene hovedplanen beskriver.

Planen ble godkjent før dokumentasjonsendringene ble gjennomført. Den Git-versjonerte app-planen er hovedkilden for videre frontend-planlegging, med egne commits for senere avklarte revisjoner. Appimplementering krever fortsatt en ny bestilling.
