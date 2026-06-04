package main

import (
	"bytes"
	"io"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgproto3"
)

// frame builds a raw Postgres backend frame: 1 type byte + 4-byte big-endian
// length (length includes itself) + payload.
func frame(typ byte, payload []byte) []byte {
	out := make([]byte, 5+len(payload))
	out[0] = typ
	length := uint32(4 + len(payload))
	out[1] = byte(length >> 24)
	out[2] = byte(length >> 16)
	out[3] = byte(length >> 8)
	out[4] = byte(length)
	copy(out[5:], payload)
	return out
}

// TestInjectionBeforeReadyForQuery feeds a raw backend byte stream (an
// AuthenticationOk 'R' frame followed by two ReadyForQuery 'Z' frames) through
// the real relayBackendToClient framer and asserts:
//   - a NoticeResponse whose Message starts with "RECUSE/0.1 deny" is written,
//   - it appears BEFORE the (first) ReadyForQuery frame,
//   - injection happens exactly once even when two ReadyForQuery frames are sent.
func TestInjectionBeforeReadyForQuery(t *testing.T) {
	notice := buildNotice("test-id-1234")

	// Raw backend stream: AuthenticationOk ('R', payload = int32 0), then two
	// ReadyForQuery ('Z', payload = 'I').
	var backendStream bytes.Buffer
	backendStream.Write(frame('R', []byte{0, 0, 0, 0}))
	backendStream.Write(frame('Z', []byte{'I'}))
	backendStream.Write(frame('Z', []byte{'I'}))

	var clientOut bytes.Buffer
	// io.EOF is expected when the source is exhausted.
	if err := relayBackendToClient(&backendStream, &clientOut, notice); err != io.EOF {
		t.Fatalf("expected io.EOF on stream exhaustion, got %v", err)
	}

	out := clientOut.Bytes()

	// Decode what the client received via a real pgproto3 Frontend.
	got := decodeBackendStream(t, out)

	noticeIdx := -1
	firstRFQIdx := -1
	noticeCount := 0
	for i, m := range got {
		switch v := m.(type) {
		case *pgproto3.NoticeResponse:
			noticeCount++
			if noticeIdx == -1 {
				noticeIdx = i
			}
			if !strings.HasPrefix(v.Message, "RECUSE/0.1 deny") {
				t.Errorf("notice Message does not start with RECUSE/0.1 deny: %q", v.Message)
			}
		case *pgproto3.ReadyForQuery:
			if firstRFQIdx == -1 {
				firstRFQIdx = i
			}
		}
	}

	if noticeIdx == -1 {
		t.Fatalf("no NoticeResponse delivered to client; got %d messages", len(got))
	}
	if firstRFQIdx == -1 {
		t.Fatalf("no ReadyForQuery delivered to client")
	}
	if noticeIdx >= firstRFQIdx {
		t.Errorf("NoticeResponse (idx %d) must come before first ReadyForQuery (idx %d)",
			noticeIdx, firstRFQIdx)
	}
	if noticeCount != 1 {
		t.Errorf("expected exactly one NoticeResponse injection, got %d", noticeCount)
	}
}

// TestFramerPassThrough asserts the framer forwards a multi-frame backend stream
// byte-for-byte, except for the single injected notice which appears immediately
// before the first 'Z' frame. Non-'Z' frames are passed through unmodified.
func TestFramerPassThrough(t *testing.T) {
	notice := buildNotice("passthrough-id")
	noticeBytes, err := notice.Encode(nil)
	if err != nil {
		t.Fatalf("encoding notice: %v", err)
	}

	// A stream of several non-'Z' frames, then a 'Z' frame, then more non-'Z'.
	authOk := frame('R', []byte{0, 0, 0, 0})
	paramStatus := frame('S', []byte("client_encoding\x00UTF8\x00"))
	backendKey := frame('K', []byte{0, 0, 0, 1, 0, 0, 0, 2})
	rfq := frame('Z', []byte{'I'})
	rowDesc := frame('T', []byte{0, 0}) // 0 fields

	var backendStream bytes.Buffer
	backendStream.Write(authOk)
	backendStream.Write(paramStatus)
	backendStream.Write(backendKey)
	backendStream.Write(rfq)
	backendStream.Write(rowDesc)

	var clientOut bytes.Buffer
	if err := relayBackendToClient(&backendStream, &clientOut, notice); err != io.EOF {
		t.Fatalf("expected io.EOF on stream exhaustion, got %v", err)
	}

	// Expected output: all frames verbatim, with noticeBytes inserted directly
	// before the first 'Z' frame.
	var want bytes.Buffer
	want.Write(authOk)
	want.Write(paramStatus)
	want.Write(backendKey)
	want.Write(noticeBytes)
	want.Write(rfq)
	want.Write(rowDesc)

	if !bytes.Equal(clientOut.Bytes(), want.Bytes()) {
		t.Errorf("framer output did not match expected byte-for-byte stream\n got: %x\nwant: %x",
			clientOut.Bytes(), want.Bytes())
	}
}

// decodeBackendStream decodes a raw backend byte stream into typed messages using
// a real pgproto3.Frontend, so tests can inspect the delivered NoticeResponse and
// ReadyForQuery messages.
func decodeBackendStream(t *testing.T, raw []byte) []pgproto3.BackendMessage {
	t.Helper()
	fe := pgproto3.NewFrontend(bytes.NewReader(raw), io.Discard)
	var got []pgproto3.BackendMessage
	for {
		msg, err := fe.Receive()
		if err != nil {
			return got
		}
		// Copy since pgproto3 may reuse buffers across Receive calls.
		switch m := msg.(type) {
		case *pgproto3.NoticeResponse:
			cp := *m
			got = append(got, &cp)
		case *pgproto3.ReadyForQuery:
			cp := *m
			got = append(got, &cp)
		default:
			got = append(got, msg)
		}
	}
}

// TestNoticeContent verifies the static fields of the Recuse Signal notice.
func TestNoticeContent(t *testing.T) {
	n := buildNotice("abc-123")
	if n.Severity != "NOTICE" || n.SeverityUnlocalized != "NOTICE" {
		t.Errorf("severity fields = %q/%q, want NOTICE/NOTICE", n.Severity, n.SeverityUnlocalized)
	}
	if n.Code != "01000" {
		t.Errorf("Code = %q, want 01000", n.Code)
	}
	if !strings.HasPrefix(n.Message, "RECUSE/0.1 deny; reason=production; scope=all-automation;") {
		t.Errorf("Message prefix wrong: %q", n.Message)
	}
	if !strings.Contains(n.Message, "id=abc-123") {
		t.Errorf("Message missing id: %q", n.Message)
	}
	if n.Detail == "" || n.Hint == "" {
		t.Errorf("Detail/Hint must be set")
	}
}

// TestNewIDFormat verifies the UUIDv4-style id format and version/variant bits.
func TestNewIDFormat(t *testing.T) {
	id := newID()
	// 8-4-4-4-12 = 36 chars with hyphens.
	if len(id) != 36 {
		t.Fatalf("id length = %d, want 36: %q", len(id), id)
	}
	parts := strings.Split(id, "-")
	if len(parts) != 5 {
		t.Fatalf("id parts = %d, want 5: %q", len(parts), id)
	}
	if len(parts[0]) != 8 || len(parts[1]) != 4 || len(parts[2]) != 4 ||
		len(parts[3]) != 4 || len(parts[4]) != 12 {
		t.Fatalf("id section lengths wrong: %q", id)
	}
	// version nibble (first char of 3rd group) must be '4'.
	if parts[2][0] != '4' {
		t.Errorf("version nibble = %c, want 4: %q", parts[2][0], id)
	}
	// variant nibble (first char of 4th group) must be one of 8,9,a,b.
	switch parts[3][0] {
	case '8', '9', 'a', 'b':
	default:
		t.Errorf("variant nibble = %c, want [89ab]: %q", parts[3][0], id)
	}

	// Two ids must differ.
	if id == newID() {
		t.Errorf("two ids collided: %q", id)
	}
}
