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
    sql = "sql"
    react = "react"
    whiteboard = "whiteboard"
    prompt = "prompt"


@dataclass(frozen=True)
class Language:
    id: LanguageId
    label: str
    extensions: tuple[str, ...]
    entry: str
    starter: dict[str, str]
    filenames: tuple[str, ...] = ()


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

SQL_QUERY = '''-- Every .csv file in this session is a table (employees, departments).
-- Write queries below and press Run. Results print as tables.

SELECT d.name AS department,
       COUNT(*) AS headcount,
       ROUND(AVG(e.salary)) AS avg_salary
FROM employees e
JOIN departments d ON d.id = e.department_id
GROUP BY d.name
ORDER BY avg_salary DESC;
'''

SQL_EMPLOYEES = '''id,name,department_id,salary,hired_on
1,Ada Lovelace,1,182000,2019-03-04
2,Grace Hopper,1,176000,2018-07-16
3,Alan Turing,2,168000,2020-01-13
4,Katherine Johnson,2,171000,2017-11-01
5,Margaret Hamilton,1,189000,2016-05-23
6,Linus Torvalds,3,154000,2021-09-27
7,Barbara Liskov,3,162000,2019-12-02
8,Edsger Dijkstra,2,158000,2022-04-18
'''

SQL_DEPARTMENTS = '''id,name,budget
1,Platform,2400000
2,Data,1800000
3,Product,1500000
'''

SQL_README = '''# SQL lab

1. Which department has the highest average salary?
2. Who was hired most recently in each department?
3. Which departments spend more than 60% of their budget on salaries?
'''

REACT_HTML = '''<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <link rel="stylesheet" href="style.css" />
    <script type="importmap">
      {
        "imports": {
          "react": "https://esm.sh/react@19.1.0",
          "react-dom/client": "https://esm.sh/react-dom@19.1.0/client?external=react",
          "htm": "https://esm.sh/htm@3.1.1"
        }
      }
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="app.js"></script>
  </body>
</html>
'''

REACT_APP = '''import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import htm from "htm";

// htm gives JSX-like templates without a build step.
const html = htm.bind(React.createElement);

function Counter({ label }) {
  const [count, setCount] = useState(0);
  return html`
    <div class="card">
      <h1>${label}</h1>
      <p>Clicked ${count} time${count === 1 ? "" : "s"}</p>
      <button onClick=${() => setCount(count + 1)}>Click me</button>
    </div>
  `;
}

createRoot(document.getElementById("root")).render(html`<${Counter} label="Hello, candidate!" />`);
console.log("React", React.version, "is ready");
'''

REACT_CSS = WEB_CSS

WHITEBOARD_SCENE = '''{
  "type": "excalidraw",
  "version": 2,
  "source": "watchmecode",
  "elements": [],
  "appState": { "viewBackgroundColor": "#ffffff" },
  "files": {}
}
'''

WHITEBOARD_NOTES = '''# System design

Design a URL shortener that handles 10,000 writes and 1,000,000 reads per second.

- API
- Data model
- Storage and caching
- Scaling and failure modes
'''

PROMPT_BRIEF = '''# Prompt lab: rate limiting

You're pairing with an AI coding agent (Cursor, Claude Code, Codex, …) on a real codebase.
Write the prompt you would give it in **prompt.md**, then press **Run**. An AI reviewer plays the
agent, shows what it would do with your prompt, and points out gaps.

## The task

Our public REST API (`POST /v1/pads`, `GET /v1/pads/{id}`) has no rate limiting. Last week one customer's
script sent 40,000 requests in ten minutes and slowed everyone down.

Get an agent to add per-API-key rate limiting.

## What you know

- Python 3.12, FastAPI, deployed on Vercel serverless functions (no long-lived process memory).
- Postgres (Supabase) is available. There is no Redis today.
- API keys look like `wmc_<workspace>.<token>` and are validated in `app/public_api.py`.
- Customers on the Team plan were promised "fair use"; Enterprise contracts mention 600 requests/minute.
- The Ashby integration calls the same API in bursts when interview schedules change.
- Our docs live in `docs/api.md`; clients currently get no rate-limit headers.

## What we look for

A clear intent and definition of done, guardrails (what the agent must not do or break), and the
context, tools, MCP servers, or integrations the agent needs to do it right.
'''

PROMPT_STARTER = '''Add rate limiting to the API so customers can't spam it.
'''

LAB_LANGUAGE_IDS = frozenset({LanguageId.sql, LanguageId.react, LanguageId.whiteboard, LanguageId.prompt})

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
        Language(
            LanguageId.sql,
            "SQL lab",
            (".sql",),
            "query.sql",
            {
                "query.sql": SQL_QUERY,
                "employees.csv": SQL_EMPLOYEES,
                "departments.csv": SQL_DEPARTMENTS,
                "README.md": SQL_README,
            },
        ),
        Language(LanguageId.react, "React lab", (), "index.html", {"index.html": REACT_HTML, "app.js": REACT_APP, "style.css": REACT_CSS}),
        Language(
            LanguageId.whiteboard,
            "Whiteboard",
            (".excalidraw",),
            "board.excalidraw",
            {"board.excalidraw": WHITEBOARD_SCENE, "notes.md": WHITEBOARD_NOTES},
        ),
        Language(
            LanguageId.prompt,
            "Prompt lab (AI)",
            (".prompt.md",),
            "prompt.md",
            {"BRIEF.md": PROMPT_BRIEF, "prompt.md": PROMPT_STARTER},
            filenames=("prompt.md",),
        ),
    )
}


def language_for_path(path: str) -> Language | None:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    for lang in LANGUAGES.values():
        if name in lang.filenames or lowered.endswith(lang.extensions):
            return lang
    return None
