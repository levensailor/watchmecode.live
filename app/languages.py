"""Supported languages (GitHub Octoverse top 10 plus a web playground) and starter files."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LanguageId(StrEnum):
    python = "python"
    javascript = "javascript"
    typescript = "typescript"
    java = "java"
    csharp = "csharp"
    cpp = "cpp"
    c = "c"
    go = "go"
    php = "php"
    shell = "shell"
    web = "web"


@dataclass(frozen=True)
class Language:
    id: LanguageId
    label: str
    extensions: tuple[str, ...]
    entry: str
    starter: dict[str, str]


PYTHON_STARTER = '''import requests


def greet(name: str) -> str:
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("candidate"))
    print("requests version:", requests.__version__)
'''

JAVASCRIPT_STARTER = '''// Imports are auto-installed with npm on Run.
function greet(name) {
  return `Hello, ${name}!`;
}

console.log(greet("candidate"));
'''

TYPESCRIPT_STARTER = '''type Candidate = { name: string };

function greet(candidate: Candidate): string {
  return `Hello, ${candidate.name}!`;
}

console.log(greet({ name: "candidate" }));
'''

JAVA_STARTER = '''public class Main {
    static String greet(String name) {
        return "Hello, " + name + "!";
    }

    public static void main(String[] args) {
        System.out.println(greet("candidate"));
    }
}
'''

CSHARP_STARTER = '''using System;

static string Greet(string name) => $"Hello, {name}!";

Console.WriteLine(Greet("candidate"));
'''

CPP_STARTER = '''#include <iostream>
#include <string>

std::string greet(const std::string& name) {
    return "Hello, " + name + "!";
}

int main() {
    std::cout << greet("candidate") << std::endl;
    return 0;
}
'''

C_STARTER = '''#include <stdio.h>

int main(void) {
    printf("Hello, %s!\\n", "candidate");
    return 0;
}
'''

GO_STARTER = '''package main

import "fmt"

func greet(name string) string {
	return fmt.Sprintf("Hello, %s!", name)
}

func main() {
	fmt.Println(greet("candidate"))
}
'''

PHP_STARTER = '''<?php

function greet(string $name): string {
    return "Hello, {$name}!";
}

echo greet("candidate") . PHP_EOL;
'''

SHELL_STARTER = '''#!/usr/bin/env bash
set -euo pipefail

greet() {
  echo "Hello, $1!"
}

greet "candidate"
'''

WEB_HTML = '''<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <link rel="stylesheet" href="style.css" />
  </head>
  <body>
    <main class="card">
      <h1>Hello, candidate!</h1>
      <button id="count">Clicked 0 times</button>
    </main>
    <script src="script.js"></script>
  </body>
</html>
'''

WEB_CSS = '''body {
  font-family: system-ui, sans-serif;
  display: grid;
  place-items: center;
  min-height: 100vh;
  margin: 0;
  background: #f5f5f7;
}

.card {
  background: #fff;
  padding: 24px 32px;
  border-radius: 20px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.06);
}

button {
  font: inherit;
  padding: 8px 16px;
  border-radius: 12px;
  border: 0;
  background: #6e56cf;
  color: #fff;
}
'''

WEB_JS = '''let clicks = 0;
const button = document.getElementById("count");
button.addEventListener("click", () => {
  clicks += 1;
  button.textContent = `Clicked ${clicks} times`;
});
'''

LANGUAGES: dict[LanguageId, Language] = {
    lang.id: lang
    for lang in (
        Language(LanguageId.python, "Python", (".py",), "main.py", {"main.py": PYTHON_STARTER}),
        Language(LanguageId.javascript, "JavaScript", (".js", ".mjs", ".cjs"), "index.js", {"index.js": JAVASCRIPT_STARTER}),
        Language(LanguageId.typescript, "TypeScript", (".ts", ".mts", ".tsx"), "index.ts", {"index.ts": TYPESCRIPT_STARTER}),
        Language(LanguageId.java, "Java", (".java",), "Main.java", {"Main.java": JAVA_STARTER}),
        Language(LanguageId.csharp, "C#", (".cs",), "Program.cs", {"Program.cs": CSHARP_STARTER}),
        Language(LanguageId.cpp, "C++", (".cpp", ".cc", ".cxx", ".hpp"), "main.cpp", {"main.cpp": CPP_STARTER}),
        Language(LanguageId.c, "C", (".c", ".h"), "main.c", {"main.c": C_STARTER}),
        Language(LanguageId.go, "Go", (".go",), "main.go", {"main.go": GO_STARTER}),
        Language(LanguageId.php, "PHP", (".php",), "index.php", {"index.php": PHP_STARTER}),
        Language(LanguageId.shell, "Shell", (".sh", ".bash"), "main.sh", {"main.sh": SHELL_STARTER}),
        Language(
            LanguageId.web,
            "HTML/CSS/JS",
            (".html", ".htm", ".css"),
            "index.html",
            {"index.html": WEB_HTML, "style.css": WEB_CSS, "script.js": WEB_JS},
        ),
    )
}


def language_for_path(path: str) -> Language | None:
    lowered = path.lower()
    for lang in LANGUAGES.values():
        if lowered.endswith(lang.extensions):
            return lang
    return None
