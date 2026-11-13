# DeWarmte Control 3.4 – prijsslim verwarmen, rustige regeling, service-instellingen en dashboard

Eén integration (geen add-on) die via MQTT met je DR164 praat, alle pompen en meters uitleest
en **zelf regelt**: aanvoersetpoint, aan/uit en vermogensmodus van de SunRain, en het tapwatersetpoint van Pomp T.

## Installatie via HACS (aanbevolen)
1. HACS → ⋮ → **Custom repositories** → URL van deze repository, type **Integration** → Toevoegen.
2. Zoek **DeWarmte Control** in HACS → Downloaden → herstart Home Assistant.
3. Updates verschijnen daarna vanzelf onder Instellingen → Updates.

## Handmatige installatie
1. Maak een back-up van Home Assistant.
2. **Stop de add-on "DeWarmte Communication Core"** en zet automatisch starten uit (één Modbus-master tegelijk).
   Zet de oude integratie `dewarmte_control_center` uit, en ook je eigen tapwater-prijsautomation
   (anders vechten twee regelingen om hetzelfde setpoint).
3. Staat versie 3.0 al geïnstalleerd: overschrijf de map. Anders: kopieer `custom_components/dewarmte` naar
   `/config/custom_components/`. Herstart Home Assistant.
4. Integratie toevoegen → **DeWarmte Control** → de MQTT-topics van je DR164 (`<MAC>/up` en `<MAC>/down`),
   kamers (Versatile Thermostats), buitensensor, prijssensor en eventueel je notify-dienst
   (bijv. `mobile_app_mijn_telefoon`, staat onder Ontwikkelhulpmiddelen → Acties → zoek "notify.").
5. De regeling start meteen. Druk op **Uurrapport nu versturen** om de melding te testen.

## Wat er geschreven wordt (en verder niets)
| Register | Wat | Grenzen | Max. |
|---|---|---|---|
| SunRain 769 | aanvoersetpoint | 20–50 °C (en je min/max) | 1× per 15 min, 4/uur, 40/dag |
| SunRain 773 | aan/uit | 0/1, min. 30 min aan / 10 min uit | 4/uur, 12/dag |
| SunRain 775 | Stil/Normaal/Boost | 0–2 | 1× per 20 min, 3/uur, 16/dag |
| Pomp T 6 | tapwatersetpoint | 40–70 °C | 1× per 30 min, 2/uur, 12/dag |

Totaal maximaal 10 per uur en 80 per dag. Beveiligingen, ontdooien, compressorlimieten enz. kunnen niet
worden geschreven: de bus weigert elk ander register, ook als de code een fout zou bevatten.
Elke schrijfactie wordt bevestigd met het Modbus-echoantwoord en daarna direct teruggelezen.
**Geen enkele wijziging** bij: storing, ontdooien of als de SunRain-data niet vers is.
Na 3 mislukte schrijfacties achter elkaar zet de regeling zichzelf uit en krijg je een melding.

**Noodstop:** zet de schakelaar **Regeling actief** uit. Dan wordt alleen nog gelezen; de pomp blijft
staan op de laatste waarden. Deze keuze blijft bewaard na een herstart.

## Regelgedrag
- Aanvoer = 31 − 0,5 × buiten + 2 K per K kamertekort (max. +6) + geleerde offset (±4), begrensd 25–45 °C.
- **Basislast (nieuw):** in het stookseizoen onder 10 °C buiten blijft de unit aan op de stooklijn, ook
  zonder TRV-vraag, omdat de Jaga's in hal, toilet en bijkeuken geen TRV hebben. Instelbaar in de opties.
- Boven 10 °C: aan bij warmtevraag (klep ≥ 10 % of tekort ≥ 0,4 K), uit na 45 min zonder vraag.
- Geen verwarming boven 18 °C buiten (hervat onder 16 °C).
- Vermogensmodus: boven 3 °C altijd Stil; eronder start Stil en schaalt op bij aanhoudend tekort; onder −9 °C minimaal Normaal.
- Pomp T: als die draait op koud bronwater (< 18 °C) gaat de pomp AO aan met minimaal 30 °C aanvoer.
- Tapwater: minimaal 45 °C, daarboven op prijs (< 0,20 → 60, < 0,26 → 55, < 0,30 → 50, anders 45, negatief → 70).

## Uurrapport
Elk heel uur (:00) een melding in Home Assistant (vervangt de vorige) en, als ingesteld, op je telefoon:
wat er is gewijzigd en waarom, gemiddelde buiten- en aanvoertemperatuur, looptijd van de compressor, verdeling
over de modi, kWh en COP van het afgelopen uur, tapwater, blokkades en uitgestelde wijzigingen.
De sensoren **Laatste actie** (laatste 30 acties als attribuut) en **Laatste uurrapport** tonen hetzelfde.

## Entiteiten
- Apparaat **DeWarmte**: ca. 45 hoofdentiteiten (metingen, beslissingen, schakelaar, knop).
- Apparaten **SunRain**, **Pomp T**, **kWh-meters** en **warmtemeters**: alle ca. 560 registers en statusbits uit de
  fabrikanttabellen (onder Diagnostiek, met registernummer in de naam). Ze zijn alleen-lezen.
  Wil je je database klein houden, sluit ze dan uit van de recorder of schakel ongebruikte uit.

## Controleer de eerste dag
- Of het **debiet** van de warmtemeters (factor 0,001667) klopt met het display van de meter.
- Of register 769 het aanvoer- of retoursetpoint is (sensor *Regelmethode unit (372)*); stel zo nodig
  de optie "Setpoint van de unit regelt op" in.
- Het uurrapport en het logboek: `Schrijfactie …` regels in het HA-log, CSV in `/config/dewarmte/`.

## Tests
`python3 tests/test_core.py` en `python3 tests/test_ha_glue.py` (geen Home Assistant nodig).

## Nieuw in 3.2
### Service-instellingen
Alle 198 schrijfbare registers van SunRain en Pomp T staan als instelling (number) bij de apparaten
*SunRain monoblock* en *Pomp T*, met de grenzen uit de fabrikanttabel. Wijzigen kan alleen als de schakelaar
**Servicemodus** aan staat; die gaat na 30 minuten vanzelf uit (na een wijziging blijft hij minimaal 10 min open).
Elke wijziging staat in het uurrapport en in `/config/dewarmte/servicelog.csv` met de oude waarde.
Niet schrijfbaar, ook niet in servicemodus: de commandobits 816/817 (ontdooien forceren, fabrieksinstellingen,
koudemiddel terugwinnen, compressor forceren) en de bytes van 780/781.

### Handmatige desinfectie
Knop **Desinfectie starten**: Pomp T gaat naar de desinfectietemperatuur (register 10, 60–70 °C) met alleen de
warmtepomp, houdt die vast zolang register 11 aangeeft (10–90 min) en zet daarna het prijs-setpoint terug.
Wordt de temperatuur binnen 4 uur niet gehaald, dan stopt hij en meldt de hoogste bereikte temperatuur.
**Desinfectie stoppen** breekt af. Tijdens de desinfectie pauzeert de prijsregeling van het tapwater.

### Dashboard
Bij elke start schrijft de integratie:
- `/config/www/dewarmte/` – het hydraulische schema en de stromingslagen (bewegen als er circulatie is);
- `/config/dewarmte/dashboard.yaml` – het dashboard met jouw echte entity-id's.

Benodigd via HACS → Frontend: **Mushroom** en **ApexCharts Card**. Voeg toe aan `configuration.yaml`:
```yaml
lovelace:
  dashboards:
    dewarmte-dashboard:
      mode: yaml
      title: DeWarmte
      icon: mdi:heat-pump
      show_in_sidebar: true
      filename: dewarmte/dashboard.yaml
```
Tabbladen: Overzicht (schema met live waarden), Grafieken, Kamers, Tapwater (met desinfectie), Service, Diagnose.

## Nieuw in 3.3: rustige regeling
Gewenste aanvoer = stooklijn + preset-correctie + klepbijsturing + geleerde offset.
- **Klepbijsturing** (vervangt de directe kamerboost): elke 30 min kijkt de regeling naar de gemiddelde stand van
  de meest open klep. ≥ 90 % én echt tekort (≥ 0,3 K) → +0,5 °C; ≤ 60 % en geen tekort → −0,5 °C; daartussen gelijk.
  Zo zakt de aanvoer tot de kamer die het meest vraagt zijn klep ver open heeft (beste COP). Grenzen −3…+4 °C.
- **Preset-correctie**: staat een kamerdoel boven het gebruikelijke doel (meest voorkomend in 24 u), bijv. door
  goedkope stroom, dan gaat de aanvoer meteen 0,5 °C per graad mee. Na elke doelwijziging wacht de klepbijsturing 60 min.
- **Kamers met TRV-sensor** (optie *Kamers die meten met de sensor in de TRV*): hun tekort wordt 30 min gemiddeld en
  telt half mee, omdat die sensor op de convector zit en snel op-en-neer gaat. Selecteer hier slaapkamer groot en klein.
- **Geleerde offset**: bij de update teruggezet naar 0 (stond vast op +4). Nu leert hij hooguit 0,5 °C per 6 uur,
  omhoog én omlaag even makkelijk, maximaal ±2 °C.
- **Setpoint-hysterese**: het setpoint van de unit verandert pas bij 0,75 °C verschil (minder heen-en-weer).
Nieuwe sensoren: *Klepbijsturing* (met reden) en *Preset-correctie*.

## Nieuw in 3.4: prijsslim verwarmen
Schakelaar **Prijsslim verwarmen** (standaard aan). De integratie haalt elk half uur de prijzen van vandaag en
morgen op via een dienst (standaard `tibber.get_prices`, instelbaar) en stuurt dan zelf:

- **Kamerdoelen (Versatile Thermostat)**: normaal doel = comfort-preset overdag, eco-preset 's nachts
  (23:00–05:00, weekend tot 07:00), de *away*-varianten als niemand thuis is (optie *Aanwezigheid*).
  Daarop een prijs-offset: is het nu goedkoper dan gemiddeld in het venster rond nu, dan voorverwarmen
  (max. +1,5 °C); is het duurder, dan iets laten zakken (max. −1 °C). Stappen van 0,5 °C, minimaal 45 min
  vasthouden. Een negatieve prijs geeft meteen maximaal voorverwarmen.
- **Venster leren**: per kamer meet de integratie hoe snel hij afkoelt met dichte klep; daaruit volgt hoe lang
  het huis een graad extra vasthoudt (2–6 uur). Hoe trager het afkoelt, hoe verder vooruit hij plant.
- **Aanvoer gaat direct mee** met de prijs-offset (0,5 °C aanvoer per °C kamerdoel).
- **Boiler**: in de goedkoopste 10 % van het etmaal naar 60 °C, in de goedkoopste 25 % naar 55 °C, anders 45 °C;
  bij negatieve prijs 70 °C.
- **Handmatig ingrijpen** (zelf een kamer hoger/lager zetten) wordt gerespecteerd tot het volgende blok
  (dag/nacht), maximaal 3 uur.

Zet de automation *Nachtverlaging thermostaat* uit zodra je 3.4 gebruikt: de integratie doet dit nu zelf.
Sensor **Prijs-offset kamers** toont de reden, het geleerde venster, de afkoelsnelheid per kamer en de
planning voor de komende uren; het tabblad Grafieken heeft een prijs/offset-grafiek en een planningstabel.
