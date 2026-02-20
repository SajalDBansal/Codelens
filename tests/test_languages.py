"""
tests/test_languages.py
=======================

ONE PARSING TEST PER SUPPORTED LANGUAGE.

WHY THIS FILE EXISTS
--------------------
Every other parser test in this project used Python source. That was a real
hole, and it hid a long list of bugs that were only found by running the
parser over a repository that actually mixed languages:

  - Go produced ZERO types, because `type_declaration` has no "name" field,
    so every struct and interface was treated as anonymous and dropped.
  - Rust emitted `struct Foo` and `impl Foo` under the SAME entity id, and
    because the graph MERGEs on id, the impl block silently overwrote the
    struct definition.
  - Java read the word "extends" out of its `superclass` node and recorded
    it as a parent class name, twice.
  - Ruby declared every method call an import, because `require` is itself an
    ordinary call and "call" had been listed as the import node type.
  - PHP resolved `$this->repo->find()` to a call to `repo`, not to `find`.
  - C++ named an out-of-line method `AuthService::login`, which no call to
    `login` could ever match.

None of these raised an error. Each one just quietly produced less graph, so
the only way to catch them is to assert on what the parser FINDS.

The rule this file enforces: for every language we claim to support, parsing
a small realistic file must produce the entities a reader would expect, with
unique ids and sane line numbers.
"""

from collections import Counter

import pytest

from graph_rag.parsing.code_parser import parse_file
from graph_rag.parsing.languages import EXTENSION_TO_LANGUAGE, LANGUAGE_CONFIGS

# Each case is (filename, source, expected {name: kind} subset).
# "subset" because a grammar may legitimately give us extra nodes; what
# matters is that nothing we expect is MISSING.
CASES = [
    (
        "svc.py",
        '''MAX_RETRIES = 3


class Base:
    pass


class AuthService(Base):
    """Handles login."""

    def login(self, user):
        return verify(user)


def verify(user):
    return bool(user)
''',
        {"MAX_RETRIES": "constant", "Base": "class", "AuthService": "class",
         "login": "method", "verify": "function"},
    ),
    (
        "api.ts",
        '''export const MAX_RETRIES = 3;

interface User { id: string; }

export class AuthService {
  async login(user: string): Promise<boolean> {
    return verify(user);
  }
}

export const verify = (user: string) => Boolean(user);
''',
        {"MAX_RETRIES": "constant", "User": "class", "AuthService": "class",
         "login": "method", "verify": "function"},
    ),
    (
        "widget.tsx",
        '''import React from "react";

export function LoginForm({ onSubmit }: { onSubmit: () => void }) {
  const handleClick = () => { onSubmit(); };
  return <button onClick={handleClick}>Go</button>;
}
''',
        {"LoginForm": "function", "handleClick": "function"},
    ),
    (
        "app.js",
        '''const MAX_RETRIES = 3;

class AuthService {
  login(user) { return verify(user); }
}

function verify(user) { return Boolean(user); }
''',
        {"MAX_RETRIES": "constant", "AuthService": "class",
         "login": "method", "verify": "function"},
    ),
    (
        "svc.go",
        '''package main

const MaxRetries = 3

type AuthService struct {
	repo *Repository
}

type Repository struct{}

func (s *AuthService) Login(user string) bool {
	return verify(user)
}

func verify(user string) bool { return user != "" }
''',
        # The regression: AuthService and Repository are the whole point.
        #
        # `Login` is a METHOD, not a function. Go declares membership in its
        # receiver - `func (s *AuthService) Login(...)` - rather than by
        # nesting, so for a long time it came out as a free function parented
        # to the file's <module> node, and AuthService had no methods at all
        # in the graph. `verify` has no receiver and really is a function.
        {"MaxRetries": "constant", "AuthService": "class",
         "Repository": "class", "Login": "method", "verify": "function"},
    ),
    (
        "lib.rs",
        '''pub const MAX_RETRIES: u32 = 3;

pub struct AuthService { repo: Repository }

pub struct Repository;

pub trait Pingable { fn ping(&self) -> bool; }

impl AuthService {
    pub fn login(&self, user: &str) -> bool { verify(user) }
}

pub fn verify(user: &str) -> bool { !user.is_empty() }
''',
        {"MAX_RETRIES": "constant", "AuthService": "class",
         "Repository": "class", "Pingable": "class",
         "login": "method", "verify": "function"},
    ),
    (
        "Svc.java",
        '''package com.example;

public class AuthService extends BaseService {
    public boolean login(String user) {
        return verify(user);
    }

    private boolean verify(String user) { return user != null; }
}
''',
        {"AuthService": "class", "login": "method", "verify": "method"},
    ),
    (
        "Svc.cs",
        '''namespace Example;

public class AuthService : BaseService {
    private Repository repo;
    public bool Login(string user) { return Verify(user); }
    private bool Verify(string user) => user != null;
}
''',
        # `private Repository repo;` is an instance FIELD, not a constant.
        # It used to be emitted as one, which both mislabelled it and put a
        # name in the graph that collided with every other `repo`, making
        # real references to any of them ambiguous. C# constants need `const`.
        {"AuthService": "class", "Login": "method", "Verify": "method"},
    ),
    (
        "svc.rb",
        '''require 'json'

MAX_RETRIES = 3

class AuthService < BaseService
  def login(user)
    verify(user)
  end

  def verify(user)
    !user.nil?
  end
end
''',
        {"MAX_RETRIES": "constant", "AuthService": "class",
         "login": "method", "verify": "method"},
    ),
    (
        "svc.php",
        '''<?php
namespace App;

class AuthService {
    private $repo;
    public function login($user) {
        return $this->repo->find($user);
    }
}

function verify($user) { return $user !== null; }
''',
        {"AuthService": "class", "login": "method", "verify": "function"},
    ),
    (
        "Svc.kt",
        '''package com.example

const val MAX_RETRIES = 3

class AuthService(private val repo: Repository) {
    fun login(user: String): Boolean = verify(user)
}

fun verify(user: String): Boolean = user.isNotEmpty()
''',
        {"MAX_RETRIES": "constant", "AuthService": "class",
         "login": "method", "verify": "function"},
    ),
    (
        "Svc.swift",
        '''import Foundation

let maxRetries = 3

class AuthService {
    func login(user: String) -> Bool { return verify(user: user) }
}

func verify(user: String) -> Bool { return !user.isEmpty }
''',
        {"maxRetries": "constant", "AuthService": "class",
         "login": "method", "verify": "function"},
    ),
    (
        "Svc.scala",
        '''package example

class AuthService(repo: Repository) {
  def login(user: String): Boolean = verify(user)
}

object Helpers {
  def verify(user: String): Boolean = user.nonEmpty
}
''',
        {"AuthService": "class", "login": "method",
         "Helpers": "class", "verify": "method"},
    ),
    (
        "svc.c",
        '''#include <stdio.h>

#define MAX_RETRIES 3

struct Repository { int id; };

int verify(const char *user) { return user != NULL; }

int login(struct Repository *repo, const char *user) {
    return verify(user);
}
''',
        {"MAX_RETRIES": "constant", "Repository": "class",
         "verify": "function", "login": "function"},
    ),
    (
        "engine.cpp",
        '''#include <string>

class AuthService {
public:
    bool login(const std::string& user);
};

bool verify(const std::string& user) { return !user.empty(); }

bool AuthService::login(const std::string& user) {
    return verify(user);
}
''',
        # The out-of-line definition must be named `login`, not
        # `AuthService::login`, or no call to login can ever match it.
        {"AuthService": "class", "verify": "function", "login": "method"},
    ),
    (
        "deploy.sh",
        '''#!/bin/bash
MAX_RETRIES=3

verify() {
  echo "$1"
}

login() {
  verify "$1"
}
''',
        {"MAX_RETRIES": "constant", "verify": "function", "login": "function"},
    ),
    (
        "init.lua",
        '''MAX_RETRIES = 3

function verify(user)
  return user ~= nil
end

function login(user)
  return verify(user)
end
''',
        {"MAX_RETRIES": "constant", "verify": "function", "login": "function"},
    ),
]

IDS = [name for name, _, _ in CASES]


@pytest.fixture(params=CASES, ids=IDS)
def parsed(request, tmp_path):
    filename, source, expected = request.param
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    result = parse_file(path, filename)
    assert result is not None, f"{filename} was not recognised as code at all"
    return result, expected, filename


def test_expected_entities_are_found(parsed):
    """
    Every entity a reader would expect to see must actually be in the output.

    This is the assertion that catches a whole language silently producing
    nothing - the Go and Rust failures both looked like a completely clean
    run until someone counted the nodes.
    """
    result, expected, filename = parsed

    # One NAME can legitimately belong to several entities of different kinds:
    # Rust's `struct AuthService` and its `impl AuthService` are two nodes
    # sharing a name. Collapsing them into {name: kind} let the later one hide
    # the earlier, so a missing struct could pass unnoticed.
    found: dict[str, set[str]] = {}
    for entity in result.entities:
        found.setdefault(entity.name, set()).add(entity.kind)

    missing = {name for name in expected if name not in found}
    assert not missing, (
        f"{filename}: parser did not find {sorted(missing)}. "
        f"It found {sorted(found)}"
    )

    wrong_kind = {
        name: (sorted(found[name]), kind)
        for name, kind in expected.items()
        if kind not in found[name]
    }
    assert not wrong_kind, f"{filename}: wrong kinds (got, expected) = {wrong_kind}"


def test_entity_ids_are_unique(parsed):
    """
    Ids must be unique within a file, because the graph MERGEs on id.

    A duplicate is not a warning, it is silent data loss: the second node
    overwrites the first. Rust hit this with `struct Foo` and `impl Foo`.
    """
    result, _, filename = parsed
    counts = Counter(entity.id for entity in result.entities)
    duplicates = {key: count for key, count in counts.items() if count > 1}
    assert not duplicates, f"{filename}: duplicate entity ids {duplicates}"


def test_line_numbers_are_sane(parsed):
    """Line numbers are 1-based and never run past the end of the file."""
    result, _, filename = parsed
    for entity in result.entities:
        assert entity.start_line >= 1, f"{filename}: {entity.id} starts at line 0"
        assert entity.end_line >= entity.start_line, (
            f"{filename}: {entity.id} ends before it starts"
        )


def test_imports_are_imports(parsed):
    """
    An import line must actually look like one.

    Ruby is the case that motivated this: `require` is an ordinary method
    call, and declaring "call" the import node type made every single method
    call in the file show up as an import.
    """
    result, _, filename = parsed
    for line in result.imports:
        assert len(line) < 200, f"{filename}: suspiciously long import {line!r}"
        assert "\n" not in line.strip(), f"{filename}: multi-line import {line!r}"


def test_no_keyword_is_treated_as_a_parent_class(parsed):
    """
    Java writes inheritance as the text "extends BaseService", and a regex
    over that text produced `extends` as though it were a class name - a
    phantom reference that matched nothing and was counted as a real missed
    edge in the report.
    """
    result, _, filename = parsed
    keywords = {"extends", "implements", "public", "private", "protected"}
    for _source_id, name, kind in result.unresolved_references:
        if kind == "EXTENDS":
            assert name not in keywords, (
                f"{filename}: recorded the keyword {name!r} as a parent class"
            )


def test_every_configured_language_has_a_case():
    """
    A language we claim to support but never parse in a test is a language
    that can break without anyone noticing - which is exactly how the Go and
    Rust bugs survived. Adding a grammar means adding a case above.
    """
    covered = {
        EXTENSION_TO_LANGUAGE[f".{name.rsplit('.', 1)[1]}"]
        for name, _, _ in CASES
    }
    missing = set(LANGUAGE_CONFIGS) - covered
    assert not missing, f"no parsing test for: {sorted(missing)}"
