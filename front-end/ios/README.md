# Predmaint: lokal iPhone-prototype

Appen tar opp WAV/PCM (48 kHz, mono, 16 bit), viser lokale opptak og spiller dem av. Den bruker ingen server, innlogging, Azure, Foundry eller Key Vault. Den analyserer ikke togtilstand.

## Status og begrensninger

Kildekode og Xcode-prosjekt er opprettet. Lagringskjernen er bygget og kontrollert på macOS med Swift 6.3.3. iOS-kildekodens syntaks og prosjektformatet er kontrollert, men full Xcode og iOS-SDK var ikke tilgjengelig på utviklingsmaskinen. iOS-typekontroll, simulatorbygg og kjøring på fysisk iPhone er derfor ikke verifisert. Ikke bruk prototypen med reelle eller sensitive feltopptak før kontrollene nedenfor er gjennomført.

Opptak stopper ved 120 sekunder, når appen forlater aktiv forgrunn, ved mikrofonbytte eller lydavbrudd. Gyldige delopptak merkes som ufullstendige. Mikrofonens systemdialog er håndtert separat fra et bakgrunnsbytte; opptak starter bare når appen er aktiv og tillatelsen er gitt.

Lyd og metadata ligger i appens private Application Support-mappe `Recordings`, med komplett filbeskyttelse og backup-ekskludering. UUID-er brukes i filnavn. Appen har ikke deling, eksport eller sletting; avinstallering kan fjerne alle opptak.

Lokal grense er 250 MB og 1 000 registrerte opptak. Før et nytt opptak reserveres 13 MB i kvotekontrollen og kreves tilsvarende ledig diskplass. Ingen tidligere opptak slettes for å frigjøre plass. Andre apper kan fortsatt bruke disk under opptak; reelle skrivefeil gir synlig feil.

Manglende eller uregistrerte lydfiler gir varsler. Uregistrerte filer bevares, men vises ikke som vellykkede opptak. Korrupte metadata og feil i filbeskyttelse blokkerer nye opptak. Det finnes ingen automatisk reparasjon eller svakere beskyttelsesfallback.

## Åpne og kjøre i Xcode

1. Installer full Xcode på Macen, og fullfør førstegangsoppsettet. Appen krever iOS 17 eller nyere; velg en Xcode-versjon som støtter telefonens iOS.
2. Åpne `front-end/ios/Predmaint.xcodeproj`. Prosjektet bruker den lokale Swift-pakken `RecordingCore`; ingen eksterne pakker lastes ned.
3. Velg target **Predmaint**, deretter **Signing & Capabilities**. Velg ditt eget Team og en unik Bundle Identifier i stedet for eksempelverdien `no.predmaint.recorder`. Ikke legg sertifikater eller private nøkler i Git.
4. Koble til og godkjenn telefonen. Aktiver Developer Mode hvis nødvendig. Velg telefonen som run destination og scheme **Predmaint**.
5. Trykk Run. Godkjenn mikrofonbruk når appen ber om det. Start med et kort, ufølsomt testopptak og spill det av.

En Apple Account/Personal Team kan brukes til egen telefon med Apples begrensninger. Simulatoren egner seg til skjermtesting, men erstatter ikke fysisk test av lyd, tillatelser, låsing eller filbeskyttelse.

Mikrofonbegrunnelsen er allerede satt i prosjektets build settings og genererte Info.plist. Ikke opprett en konkurrerende Info.plist eller aktiver Background Audio.

## Kommandolinjekontroller

Fra repoets rot kan lagringskontrollene kjøres uten full Xcode:

```bash
swift run --package-path front-end/ios/RecordingCore RecordingCoreChecks
```

Dette er en Foundation-basert kontrollrunner som returnerer feilstatus ved første feil. Den trenger verken XCTest, simulator eller mikrofon. Den kontrollerer lagring, konsistens, sti-/symlinkavvisning, metadata, varighet, kvote og feilpropagering. Den verifiserer ikke iOS-opptak eller maskinvarens filbeskyttelse.

På en Mac med full Xcode kan simulatorbygg kontrolleres slik:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcodebuild -project front-end/ios/Predmaint.xcodeproj \
  -scheme Predmaint -configuration Debug \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath front-end/ios/DerivedData \
  CODE_SIGNING_ALLOWED=NO build
```

Juster `DEVELOPER_DIR` hvis Xcode er installert et annet sted. Bygget ovenfor er ikke gjennomført i denne leveransen.

## Fysisk kontroll før bruk

| Kontroll | Forventet resultat |
|---|---|
| Start/stopp og avspilling i flymodus | Registrert opptak med hørbar lyd, ingen nettverksavhengighet |
| Avslutt og åpne appen igjen | Samme opptaksliste og fungerende avspilling |
| La opptak nå grensen | Automatisk stopp; faktisk filvarighet maksimalt 120 sekunder |
| Raske knappetrykk | Ingen overlappende lydaktivitet |
| Nekt mikrofon, endre i Innstillinger | Forståelig feil og oppdatert tillatelse |
| Lås skjermen, bytt app eller avbryt lydøkten | Ingen bakgrunnsopptak; delopptak eller eksplisitt lagringsfeil |
| Bytt mikrofontilkobling | Kontrollert stopp, aldri skjult gjenopptakelse |
| Korrupt register eller manglende fil i testcontainer | Synlig feil/varsel; tidligere filer bevares |
| Nå lagringsgrensen i testcontainer | Nye opptak nektes; ingen automatisk sletting |
| Kontroller lyd og metadata i testcontainer | `NSFileProtectionComplete` og backup-ekskludering |

Prototypen setter og kontrollerer beskyttelsesattributter, men atferden ved fysisk låsing må fortsatt testes på enheten. Kontroller også faktisk WAV-format. Ikke svekk beskyttelsen hvis lagring ved låsing feiler; rapporter feilen og rett flyten før feltbruk.

## Kodekart og risikogrenser

- `Predmaint/RecordingView.swift`: norsk SwiftUI-skjerm og scene-livssyklus.
- `Predmaint/AudioController.swift`: tillatelse, lydøkt, opptak, avspilling og iOS-filbeskyttelse.
- `RecordingCore/Sources/RecordingCore/RecordingStore.swift`: metadata, konsistens, filreferanser og lagringsgrenser.
- `RecordingCore/Tests/RecordingCoreTests/RecordingStoreTests.swift`: kjørbare lagringskontroller.

Verdier som beskyttes er lokal lyd, metadata og opptakenes tilgjengelighet. Tillitsgrenser er mikrofontillatelsen, filsystemet, låst/ulåst telefon og utviklingsmaskin/Git. Opptak beviser ikke hvem eller hvilket tog lyden kommer fra. Ingen tjenestenøkler, telemetri eller rålydlogg inngår. [Hovedplanen](../../plan-predmaint.md) beskriver sikkerhetsportene før en senere Azure-/feltpilot.
