# 16 AD_Weryfikacja

**Warstwa:** Weryfikacja  
**Model w badaniu:** `claude-opus-4-7`  
**Limit odpowiedzi w badaniu:** 4096 tokenów  
**Skrypt kernela:** `AD_Petla` (wejście: `vp_message`, wyjście: `vp_parse`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_Weryfikacja`.
2. Instructions: wklej całą zawartość `instrukcje/16_AD_Weryfikacja.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | `prompt_text` z `vp_message`: podpisy zrzutów w kolejności obrazów, pusta linia, lista heurystyk |
| `zrzut_1` | Image or document | pierwszy artefakt z listy `screenshots` |
| `zrzut_2` | Image or document | drugi artefakt |
| `zrzut_3` | Image or document | trzeci artefakt |

## Uwagi

W badaniu obrazy i podpisy były przeplatane w jednej wiadomości; tu obrazy są osobnymi wejściami, a podpisy idą w tej samej kolejności na początku tekstu. Limity Copilot Studio: pliki razem < 25 MB, dokument < 50 stron, przetwarzanie do 100 s. Jeśli klaster ma mniej artefaktów, usuń zbędne wejścia albo podaj ten sam plik.

Długość instrukcji: 2 675 znaków.
