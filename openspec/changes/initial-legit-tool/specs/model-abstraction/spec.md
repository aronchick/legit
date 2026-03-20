# Delta for Model Abstraction

## ADDED Requirements

### Requirement: litellm CustomLLM Integration
The system SHALL invoke LLM models through litellm's `CustomLLM` interface, with implementations that shell out to authenticated CLI tools.

#### Scenario: Invoking via Gemini CLI
- **WHEN** the configured provider is `gemini`
- **THEN** the system SHALL invoke the `gemini` CLI with the system prompt and user prompt via litellm's CustomLLM interface
- **AND** capture stdout as the model response
- **AND** return a litellm-compatible `ModelResponse`

#### Scenario: Invoking via Claude CLI
- **WHEN** the configured provider is `claude`
- **THEN** the system SHALL invoke the `claude` CLI with the system prompt and user prompt via litellm's CustomLLM interface
- **AND** capture stdout as the model response

#### Scenario: Invoking via Codex CLI
- **WHEN** the configured provider is `openai` or `codex`
- **THEN** the system SHALL invoke the `codex` CLI with the system prompt and user prompt via litellm's CustomLLM interface
- **AND** capture stdout as the model response

### Requirement: CustomLLM Provider Registration
The system SHALL register CLI-backed providers with litellm's custom provider map.

#### Scenario: Provider registration at startup
- **WHEN** the system initializes
- **THEN** it SHALL register custom providers via `litellm.custom_provider_map`
- **AND** each provider SHALL map to a `CLIBackedProvider` instance configured for that CLI

#### Scenario: Model runner selection from config
- **WHEN** the system needs to invoke a model
- **THEN** it SHALL read `model.provider` from config to determine which CLI-backed provider to use
- **AND** optionally pass `model.name` if specified
- **AND** pass `model.temperature` if specified (default 0.3)

### Requirement: Structured Output via Prompt + Validation
The system SHALL request structured JSON output through prompting and validate with pydantic.

#### Scenario: Requesting structured output
- **WHEN** a system component needs structured output (e.g., review generation, map phase)
- **THEN** the system SHALL include the expected JSON schema in the prompt
- **AND** instruct the model to respond with valid JSON matching the schema

#### Scenario: Valid structured output
- **WHEN** the model returns output that parses as valid JSON matching the pydantic schema
- **THEN** the system SHALL parse it into the appropriate pydantic model and proceed

#### Scenario: Malformed output with repair
- **WHEN** the model returns output that does not parse as valid JSON
- **THEN** the system SHALL send the output back to the model with the prompt: "Your previous response was not valid JSON. Here is the error: {error}. Please fix and return valid JSON matching the schema."
- **AND** retry up to 2 times

#### Scenario: Repair exhausted
- **WHEN** all repair attempts fail
- **THEN** the system SHALL attempt best-effort extraction from the raw output
- **AND** log a warning with the raw output for debugging
- **AND** if best-effort extraction fails, raise an error with context

### Requirement: CLI Availability Check
The system SHALL verify that the configured LLM CLI is installed and accessible before attempting inference.

#### Scenario: CLI found
- **WHEN** the system checks for the configured CLI
- **AND** the CLI is installed and on PATH
- **THEN** the system SHALL proceed normally

#### Scenario: CLI not found
- **WHEN** the system checks for the configured CLI
- **AND** the CLI is not installed or not on PATH
- **THEN** the system SHALL fail with a clear error message naming the missing CLI
- **AND** suggest installation instructions if known

### Requirement: Inference Error Handling
The system SHALL handle LLM CLI failures gracefully.

#### Scenario: CLI returns non-zero exit code
- **WHEN** the LLM CLI returns a non-zero exit code
- **THEN** the system SHALL capture stderr
- **AND** display the error to the user with context about which step failed

#### Scenario: CLI times out
- **WHEN** the LLM CLI does not respond within a configurable timeout (default: 5 minutes)
- **THEN** the system SHALL terminate the process
- **AND** display an error suggesting the prompt may be too large or the model may be overloaded

#### Scenario: Retry on transient failure
- **WHEN** the LLM CLI fails with a transient error (timeout, rate limit)
- **THEN** the system SHALL retry with exponential backoff up to 3 times via litellm's built-in retry logic
