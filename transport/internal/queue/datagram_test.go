package queue

import (
	"context"
	"errors"
	"testing"
)

func TestDatagramQueueBoundsAndCopies(t *testing.T) {
	t.Parallel()
	q, err := NewDatagramQueue(1, 4)
	if err != nil {
		t.Fatal(err)
	}
	original := []byte{1, 2, 3}
	if err = q.Push(original); err != nil {
		t.Fatal(err)
	}
	original[0] = 9
	if err = q.Push([]byte{4}); !errors.Is(err, ErrFull) {
		t.Fatalf("full queue error = %v", err)
	}
	item, err := q.Pop(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if item.Payload[0] != 1 {
		t.Fatalf("queue retained caller storage: %v", item.Payload)
	}
	if err = q.Push(make([]byte, 5)); !errors.Is(err, ErrTooBig) {
		t.Fatalf("oversize error = %v", err)
	}
	q.Close()
	if err = q.Push(nil); !errors.Is(err, ErrClosed) {
		t.Fatalf("closed error = %v", err)
	}
}
