# Plan: Første lokale iPhone-app

## Mål og avgrensning

Teamet kjenner backend og Azure, men er nybegynnere på iOS. Første app skal lære oss mikrofontilgang, lydopptak, lokal lagring og avspilling før vi kobler til Azure.

Appen skal kunne starte og stoppe opptak, vise lagrede opptak og spille dem av uten nett. Opptak skjer bare mens appen er i forgrunnen, med maksimalt 120 sekunder og WAV-format. Den skal aldri analysere lyd eller trekke konklusjoner om togets tilstand.

Utenfor første versjon: Azure, Foundry, innlogging, opplasting, bakgrunnsopptak, deling, eksport, redigering, avansert bølgeform, automatisk sletting og feltmetadata. Dette er en læringsapp, ikke en operativ feltpilot.

Denne leveransen oppretter bare struktur og dokumentasjon. Implementering av appen krever en ny bestilling. [Hovedplanen](../plan-predmaint.md) beskriver den langsiktige løsningen.

## Teknologi og prosjektstruktur

Bruk Swift, SwiftUI og AVFoundation uten tredjepartsbiblioteker. Foreslått minimum er iOS 17 for moderne mikrofontillatelses-API; bekreft dette mot testtelefon og Xcode før implementering.

Et senere Xcode-prosjekt skal ligge i `front-end/ios/`. Hold første utforming liten:

| Del | Ansvar |
|---|---|
| Appstart | Opprette delt app-/lydtilstand og hovedskjerm |
| Hovedskjerm | Start/stopp, varighet, opptaksliste, avspilling og feil |
| Opptaksmodell | ID, filreferanse, tidspunkt, varighet og avbruddsårsak |
| Lydkontroller | Mikrofontillatelse, lydøkt, opptak og avspilling |
| Lokal lagring | Lydfiler, metadata og konsistenskontroll |

Ikke innfør omfattende lagdeling, dependency injection-rammeverk eller serverkontrakter.

## Brukerflyt

1. Appen viser «Start opptak» og tidligere opptak.
2. Ved første start forklarer appen mikrofonbruk og ber om tillatelse.
3. Under opptak vises «Stopp», varighet og tydelig status. Avspilling er deaktivert.
4. Manuell stopp eller 120 sekunder avslutter og lagrer opptaket før det vises i listen.
5. En listeoppføring viser automatisk navn/tidspunkt og varighet. Brukeren kan starte og stoppe avspilling.
6. Lagrede opptak finnes fortsatt etter at appen avsluttes og åpnes igjen.

Bare én lydaktivitet kan være aktiv. Raske knappetrykk skal ikke starte overlappende opptak eller avspilling.

Når appen går i bakgrunnen, skjermen låses eller lydøkten avbrytes, stoppes opptaket kontrollert. Lagre et gyldig delopptak hvis filen kan ferdigstilles; vis årsaken og merk det som ufullstendig. Gjenoppta aldri opptak automatisk. Hvis lagring feiler, skal appen ikke hevde at opptaket er lagret.

## Lyd og mikrofontillatelse

Bruk `AVAudioRecorder` for opptak, `AVAudioPlayer` for avspilling og `AVAudioSession` for lydøkt. Bruk `AVAudioApplication.requestRecordPermission` ved iOS 17 eller nyere.

Sett `NSMicrophoneUsageDescription` i targetens Info.plist-konfigurasjon med en forståelig norsk begrunnelse, eksempelvis: «Appen trenger mikrofonen for å ta opp lyd som du lagrer og spiller av på telefonen.» Ved generert Info.plist legges dette til via targetens Info/build settings, ikke en konkurrerende håndskrevet plist.

Målformat er ukomprimert PCM i WAV, mono, 48 kHz og 16 bit. Verifiser faktisk format og innspillingsrute på fysisk iPhone; ikke anta at maskinvaren alltid gir forespurt format. Et maksimalt opptak gir omtrent 11,52 MB lyddata. Telefonen er ikke en kalibrert lydtrykksmåler.

Ingen Background Audio capability aktiveres i denne versjonen.

## Lokal lagring og personvern

Lagre lyd og et lite metadataregister i appens private Application Support-område, ikke i caches, repoet eller en offentlig mappe. Bruk UUID-er i filnavn og dato i visningsnavn.

Metadata inkluderer ID, relativ filreferanse, tidspunkt, faktisk varighet og eventuell avbruddsårsak. Skriv metadata atomisk og kontroller registeret mot lydfilene ved oppstart. Manglende filer, uregistrerte opptak og korrupte metadata må håndteres eksplisitt, ikke skjules som en tom liste.

Bruk iOS Data Protection og ekskluder prototypeopptak fra backup. Appen har ingen skybackup eller eksport; avinstallering kan slette alle lokale opptak. Ikke logg lydinnhold eller personopplysninger. Lokale data på utviklingsmaskinen legges i `front-end/local-data/`, som ignoreres av Git.

Test i et kontrollert miljø, ikke ved jernbanesporet. Opptak av andre personer krever riktig grunnlag. Feltbruk krever godkjenningene og prosedyrene i hovedplanen.

### Cyberrisiko fra første versjon

Hovedplanens høyrisikopremiss gjelder også læringsappen, uten å utvide omfanget til Azure. Før implementering beskrives trusler mot telefon, opptak, metadata, utviklingsmaskin og Git. Bruk en telefon med enhetskode og ufølsomme testopptak frem til lokale sikkerhetskontroller er verifisert.

For forgrunnsappen skal lyd og metadata bruke komplett filbeskyttelse (`NSFileProtectionComplete`). Kontroller at lagring og kontrollert stopp ved låsing fungerer med dette; en fil som ikke kan ferdigstilles må gi synlig feil, ikke svakere beskyttelse i stillhet. Verifiser backup-ekskludering for begge datatyper, begrens lokal lagringsbruk og hindre at filreferanser peker utenfor appens opptaksområde. Manglende plass gir eksplisitt feil, ikke sletting av tidligere opptak.

Appen får aldri Azure-/Foundry-nøkler eller tilgang til Azure Key Vault. Key Vault brukes i den senere backenden. Ved senere innlogging brukes PKCE uten klienthemmelighet; nødvendige lokale tokener lagres i Keychain med bevisst valgt tilgangsklasse. Den første lokale versjonen trenger ingen autentiseringstokener.

Ingen rålyd eller sensitive metadata skal inn i Git, logger, analyseverktøy eller CI-artefakter. Før bruk med reelle data kreves kontroll av faktisk filbeskyttelse, backup, mikrofontillatelse, avbrudd og logger på fysisk iPhone.

## Tilstander og feil

Tilstander som klar, ber om mikrofontilgang, tar opp, lagrer, spiller av og feil styrer knappene. Bekreft returverdier, delegates og feil fra lyd-/fil-API-er før UI viser suksess.

| Situasjon | Forventet oppførsel |
|---|---|
| Mikrofontillatelse nektes | Forklar og vis vei til Innstillinger; ingen tom fil eller gjentatte systemprompter |
| Tillatelse endres i Innstillinger | Oppdater tilgangsstatus når appen kommer tilbake |
| Full disk eller lagringsfeil | Tydelig feil; tidligere opptak bevares |
| Mikrofon eller lydøkt utilgjengelig | Ikke start; vis forståelig årsak |
| Bakgrunn, låsing eller lydavbrudd | Kontrollert stopp og tydelig status; gyldig delopptak merkes |
| Ugyldig eller manglende lydfil | Avspilling feiler synlig uten falsk suksess |
| Uventet avslutning under lagring | Kontroller metadata og filer ved neste oppstart |

## Nybegynnerveiledning for senere implementering

Swift er programmeringsspråket, SwiftUI bygger skjermene, og AVFoundation håndterer lyd. Xcode er utviklingsverktøyet. Et target beskriver hva som bygges; bundle identifier identifiserer appen, og signing gjør at den kan kjøres på enheten. Simulatoren etterligner en iPhone, men erstatter ikke fysisk lydtesting.

1. Installer Xcode på en kompatibel Mac og fullfør førstegangsoppsettet. Kontroller Xcode-/iOS-kompatibilitet mot testtelefonen.
2. Opprett et iOS App-prosjekt med Swift og SwiftUI under `front-end/ios/`. Unngå et eget nestet Git-repo dersom veiviseren tilbyr det.
3. Velg en unik bundle identifier og team under Signing & Capabilities. En Apple Account/Personal Team kan brukes til testing på egen telefon med Apples begrensninger; betalt medlemskap er ikke et ubetinget krav for første lokale test.
4. Koble telefonen til Macen, godkjenn tillit og aktiver Developer Mode når nødvendig. Velg telefonen som run destination.
5. Legg til mikrofonbegrunnelsen, bygg og kjør. Bruk simulatoren for UI og fysisk iPhone for mikrofon, lydformat, avbrudd og lagring.
6. Ikke aktiver bakgrunnslyd eller legg inn Azure-/nettverkskonfigurasjon.

Opprett og del Xcode-prosjektfiler og nødvendige shared schemes i Git. Ikke versjoner utviklerens personlige Xcode-tilstand, build-output, sertifikater eller hemmeligheter.

## Implementeringsrekkefølge ved ny bestilling

1. Opprette Xcode-prosjekt og kjøre en tom SwiftUI-skjerm på fysisk iPhone.
2. Legge til mikrofontillatelse og fungerende start/stopp med 120-sekundersgrense.
3. Lagre filer og metadata, og vise opptaksliste etter omstart.
4. Legge til avspilling og hindre overlappende lydaktivitet.
5. Håndtere bakgrunn, låsing, avbrudd og lagrings-/tillatelsesfeil.
6. Verifisere akseptansekriteriene og dokumentere hvordan appen bygges og kjøres.

## Akseptansekriterier

- Opptak kan startes, stoppes, listes og spilles av uten nett.
- Lagrede opptak finnes etter omstart av appen.
- Automatisk stopp håndhever 120 sekunder; lagret varighet kontrolleres.
- Opptak og avspilling overlapper ikke, også ved raske knappetrykk.
- Nektet mikrofontilgang og senere endring i Innstillinger håndteres.
- Bakgrunn, skjermlås og lydavbrudd gir kontrollert stopp og synlig status.
- Feil i lagring, metadata og lydfiler gir ingen falsk suksess eller stille tap av tidligere opptak.
- Lyd og metadata har komplett filbeskyttelse og backup-ekskludering; låsing testes på fysisk telefon.
- Ugyldige filreferanser avvises, lokal lagringsgrense gir synlig feil, og logger inneholder ikke sensitive opptaksdata.
- Appen inneholder ingen tjenestenøkler og gjør ingen kall til Key Vault, Azure eller Foundry i lokalversjonen.

Ved implementering brukes målrettede tester av metadata/lagring og tilstandsoverganger, Xcode-build og manuell lydtest på fysisk telefon. Denne dokumentasjonsleveransen krever ingen app-build.

## Git-historikk

Denne filen er hovedkilden for frontend-planlegging. Hver videre avklart planrevisjon får en egen beskrivende commit og push. Ikke skriv om historikken uten eksplisitt ønske.

Bruk `git log --follow -- front-end/plan-iphone.md` for revisjoner. Hovedplanen beholdes i prosjektroten og oppdateres når avklaringer påvirker den langsiktige løsningen.
