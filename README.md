<p align="center">
  <img src="assets/banner.svg" alt="Service Dependency Mapper" width="100%">
</p>

<p align="center">
  <a href="https://github.com/FgSousace/Service-Dependency-Mapper/actions/workflows/tests.yml"><img src="https://github.com/FgSousace/Service-Dependency-Mapper/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <img src="https://img.shields.io/badge/version-1.1.0-22c55e" alt="Version 1.1.0">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/checks-ICMP%20%7C%20DNS%20%7C%20TCP%20%7C%20TLS%20%7C%20HTTP-06b6d4" alt="ICMP DNS TCP TLS HTTP">
  <img src="https://img.shields.io/badge/GUI-Tkinter-34d399" alt="Desktop GUI">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
</p>

<p align="center">
  <strong>Mapowanie zależności usług, aktywne testy dostępności i szybkie wskazywanie pierwotnej przyczyny awarii.</strong><br>
  Vendor-neutral • Desktop GUI • ICMP • DNS • TCP • TLS • HTTP
</p>

<p align="center">
  <a href="#-szybki-start">Szybki start</a> •
  <a href="#%EF%B8%8F-graficzny-interfejs">GUI</a> •
  <a href="#-jak-to-działa">Jak to działa</a> •
  <a href="#%EF%B8%8F-konfiguracja">Konfiguracja</a> •
  <a href="#-polecenia">Polecenia</a> •
  <a href="#-testy-i-jakość">Testy</a>
</p>

---

## O projekcie

W środowisku NOC jedna awaria często generuje wiele alertów. Niedostępny DNS
może spowodować błędy połączeń TCP i alarmy HTTP, chociaż rzeczywisty problem
jest tylko jeden.

**Service Dependency Mapper** wykonuje aktywne testy komponentów, analizuje
zadeklarowane między nimi zależności i rozdziela:

- **ROOT CAUSE** — prawdopodobne źródło awarii,
- **IMPACTED** — komponent dotknięty awarią zależności,
- **HEALTHY** — komponent oraz jego zależności działają poprawnie.

Narzędzie jest przeznaczone do diagnostyki i automatyzacji runbooków. Nie
zastępuje pełnego systemu monitoringu, ale pomaga ograniczyć szum alertowy i
szybciej rozpocząć właściwą eskalację.

> [!IMPORTANT]
> Projekt jest **niezależny od producenta i systemu monitoringu**. Nie jest
> panelem do Zabbixa. Możesz opisać dowolne routery, firewalle, serwery, bazy
> danych, strony, API, bramy VPN, certyfikaty i inne usługi dostępne przez
> obsługiwane kontrole. Zabbix znajduje się wyłącznie w jednym opcjonalnym
> przykładzie laboratoryjnym.

## Najważniejsze funkcje

| Funkcja | Działanie |
|---|---|
| 🧩 Mapa zależności | Definiowanie dowolnych łańcuchów i rozgałęzień w YAML |
| 🖥️ Desktop GUI | Wybór mapy, gotowy szablon, analiza i eksport bez wpisywania komend |
| 📡 Kontrole ICMP | Sprawdzanie osiągalności hostów przez systemowe polecenie ping |
| 🌐 Kontrole DNS | Rozwiązywanie nazw i opcjonalna weryfikacja oczekiwanych adresów |
| 🔌 Kontrole TCP | Sprawdzanie dostępności portu z limitem czasu |
| 🔐 Kontrole TLS | Weryfikacja handshake, certyfikatu, nazwy hosta i daty wygaśnięcia |
| ❤️ Kontrole HTTP | Weryfikacja statusu, przekierowania i oczekiwanej treści |
| 🎯 Analiza przyczyny | Oddzielanie awarii źródłowej od jej dalszych skutków |
| ⚡ Równoległe wykonanie | Jednoczesne sprawdzanie wielu komponentów |
| 📊 Raporty | Czytelna tabela terminalowa oraz stabilny schemat JSON |
| 🗺️ Eksport grafu | Mermaid i Graphviz DOT bez dodatkowych bibliotek |
| ✅ Walidacja | Brakujące zależności, duplikaty i cykle są wykrywane przed testami |
| 🤖 Automatyzacja | Jednoznaczne kody wyjścia dla CI, skryptów i runbooków |

## 🚀 Szybki start

### Wymagania

- Python **3.10 lub nowszy**
- Git
- Windows PowerShell
- Tkinter — dołączony do standardowej instalacji Pythona dla Windows

Każde polecenie uruchom jako osobny wiersz:

```powershell
Set-Location $HOME
git clone https://github.com/FgSousace/Service-Dependency-Mapper.git
Set-Location Service-Dependency-Mapper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Jeśli PowerShell zablokuje aktywację środowiska, zezwól na nią tylko w
bieżącym oknie:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

## 🖥️ Graficzny interfejs

Najprostszy sposób użycia:

```powershell
sdmap gui
```

Możesz również od razu wskazać dowolną mapę infrastruktury:

```powershell
sdmap gui examples\infrastructure-stack.yaml
```

Alternatywna komenda uruchamiająca dokładnie ten sam interfejs:

```powershell
sdmap-gui
```

GUI umożliwia:

- wybranie dowolnego pliku YAML,
- utworzenie nowego, neutralnego szablonu usługi,
- walidację grafu bez wykonywania połączeń,
- równoległe uruchomienie wszystkich kontroli,
- filtrowanie wzrokiem wyników `HEALTHY`, `IMPACTED` i `ROOT CAUSE`,
- podgląd komunikatu, zależności i surowych szczegółów komponentu,
- zmianę timeoutu i liczby workerów,
- eksport raportu JSON oraz grafu Mermaid lub Graphviz.

Analiza działa w osobnym wątku, dlatego okno pozostaje responsywne również
podczas oczekiwania na timeouty sieciowe.

<p align="center">
  <img src="assets/gui-preview.svg" alt="Podgląd graficznego interfejsu Service Dependency Mapper" width="100%">
</p>

## ⌨️ Tryb terminalowy

Sprawdź poprawność przykładowej mapy:

```powershell
sdmap validate examples\healthy-demo.yaml
```

Uruchom kontrolowaną symulację awarii:

```powershell
sdmap check examples\outage-demo.yaml
```

## 🔍 Przykładowy wynik

```text
Service Dependency Mapper
Service: Simulated checkout outage
Overall: DOWN

COMPONENT     CHECK  TARGET                              RESULT  DIAGNOSIS
------------  -----  ----------------------------------  ------  ----------
Checkout DNS  DNS    checkout-does-not-exist.invalid     DOWN    ROOT CAUSE
Checkout TLS  TCP    checkout-does-not-exist.invalid:443 DOWN    IMPACTED
Checkout API  HTTP   https://checkout.../health          DOWN    IMPACTED

Root cause candidate(s): checkout_dns
```

Zamiast trzech niezależnych alarmów operator otrzymuje jednego kandydata na
przyczynę oraz listę usług, na które ta awaria wpływa.

## 🧠 Jak to działa?

```mermaid
flowchart LR
    dns["Public DNS"] --> tcp["TLS endpoint"]
    tcp --> api["Application API"]
    database["Database"] --> api
    api --> web["Web frontend"]
```

1. Plik YAML jest walidowany przed wykonaniem jakiegokolwiek połączenia.
2. Narzędzie odrzuca nieznane zależności, powtórzone identyfikatory i cykle.
3. Kontrole ICMP, DNS, TCP, TLS i HTTP są wykonywane współbieżnie.
4. Dla każdego błędu analizowane są wszystkie zależności nadrzędne.
5. Awaria bez niedziałającego upstreamu zostaje kandydatem na **ROOT CAUSE**.
6. Pozostałe elementy łańcucha otrzymują status **IMPACTED**.
7. Wynik jest zwracany w terminalu lub zapisywany jako JSON.

> [!NOTE]
> Określenie „root cause” oznacza najlepszego kandydata wynikającego z
> zadeklarowanego grafu i przeprowadzonych testów. Ostateczna diagnoza nadal
> należy do operatora.

## ⚙️ Konfiguracja

```yaml
version: 1

service:
  name: Customer portal
  description: Public frontend with API dependency

defaults:
  timeout: 3
  workers: 8

components:
  - id: public_dns
    name: Public DNS
    check:
      type: dns
      target: portal.example.com

  - id: frontend_tls
    name: Frontend TLS
    depends_on: [public_dns]
    check:
      type: tcp
      host: portal.example.com
      port: 443

  - id: frontend_http
    name: Frontend health
    depends_on: [frontend_tls]
    check:
      type: http
      url: https://portal.example.com/health
      expected_status: [200]
      contains: healthy
```

### Wspólne pola komponentu

| Pole | Wymagane | Opis |
|---|---:|---|
| `id` | tak | Stabilny identyfikator: małe litery, cyfry, `_` i `-` |
| `name` | nie | Czytelna nazwa; domyślnie wartość `id` |
| `depends_on` | nie | Lista komponentów wymaganych przez dany element |
| `critical` | nie | Czy awaria ma ustawić całą usługę jako `DOWN`; domyślnie `true` |
| `tags` | nie | Dowolne etykiety uwzględniane w raporcie JSON |
| `check.timeout` | nie | Limit czasu komponentu nadpisujący wartość domyślną |

### Obsługiwane kontrole

| Typ | Pola |
|---|---|
| `icmp` | `target`, opcjonalnie `count` |
| `dns` | `target`, opcjonalnie `expected_addresses` |
| `tcp` | `host`, `port` |
| `tls` | `host`, opcjonalnie `port`, `server_name`, `min_days_remaining` |
| `http` | `url`, `expected_status`, opcjonalnie `contains`, `method` |

## 💻 Polecenia

### Uruchomienie GUI

```powershell
sdmap gui
```

### Uruchomienie kontroli

```powershell
sdmap check examples\healthy-demo.yaml
```

### Raport JSON

```powershell
sdmap check examples\infrastructure-stack.yaml --format json --output reports\noc.json
```

### Nadpisanie czasu i współbieżności

```powershell
sdmap check service.yaml --timeout 5 --workers 12
```

### Walidacja bez połączeń sieciowych

```powershell
sdmap validate service.yaml
```

### Eksport grafu Mermaid

```powershell
sdmap graph service.yaml --format mermaid --output service-map.mmd
```

### Eksport Graphviz DOT

```powershell
sdmap graph service.yaml --format dot --output service-map.dot
```

### Kody wyjścia

| Kod | Znaczenie |
|---:|---|
| `0` | Wszystkie komponenty krytyczne są zdrowe |
| `1` | Usługa ma stan `DOWN` albo `DEGRADED` |
| `2` | Błędna konfiguracja lub problem wejścia/wyjścia |
| `130` | Przerwanie przez użytkownika |

## 🧪 Testy i jakość

Projekt ma **53 testy jednostkowe** obejmujące:

- walidację konfiguracji i typów kontroli,
- brakujące zależności, duplikaty i cykle,
- prawidłowy porządek zależności,
- wyniki ICMP, DNS, TCP, TLS i HTTP,
- parsowanie ustawień GUI i generowanie nowego szablonu,
- analizę root cause i propagację wpływu,
- niezależne awarie oraz komponenty niekrytyczne,
- raporty terminalowe i JSON,
- eksport Mermaid i Graphviz,
- interfejs CLI i kody błędów.

```powershell
python -m pip install -e ".[dev]"
ruff check .
python -m unittest discover -s tests -v
```

GitHub Actions wykonuje lint i testy na Windowsie oraz Ubuntu dla Pythona
3.10 i 3.12.

## 📁 Struktura

```text
Service-Dependency-Mapper/
├── .github/workflows/tests.yml
├── assets/banner.svg
├── assets/gui-preview.svg
├── examples/
│   ├── healthy-demo.yaml
│   ├── infrastructure-stack.yaml
│   ├── outage-demo.yaml
│   └── zabbix-lab.yaml
├── src/service_dependency_mapper/
│   ├── analyzer.py
│   ├── checks.py
│   ├── cli.py
│   ├── config.py
│   ├── graph.py
│   ├── gui.py
│   ├── models.py
│   └── reporting.py
├── tests/
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml
└── README.md
```

## 🛡️ Bezpieczeństwo i ograniczenia

- Narzędzie wykonuje wyłącznie aktywne kontrole odczytowe.
- Nie uruchamia komend z konfiguracji i nie zmienia monitorowanych systemów.
- W plikach YAML nie należy umieszczać haseł, tokenów ani innych sekretów.
- Testuj wyłącznie hosty i usługi, do których masz uprawniony dostęp.
- Kontrola DNS korzysta z resolvera systemowego; czas jej wykonania może
  zależeć od konfiguracji systemu operacyjnego.
- Kontrola ICMP korzysta z systemowego narzędzia `ping`, a nie z uprawnień do
  surowych gniazd.

## 🗺️ Roadmapa

- eksport metryk Prometheus,
- szablony runbooków dla typowych awarii,
- opcjonalne adaptery do Zabbix API, Prometheusa i innych źródeł,
- historyczne porównywanie wyników,
- filtrowanie i wyszukiwanie w dużych mapach GUI.

## 📄 Licencja

Projekt jest dostępny na licencji [MIT](LICENSE).

---

<p align="center">
  Stworzone przez <a href="https://github.com/FgSousace">FgSousace</a>
</p>
