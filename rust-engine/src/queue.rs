/// Bounded, non-blocking work-queue backed by a crossbeam channel.
///
/// Distinct from [`crate::buffer::BufferPool`], which manages pre-allocated memory
/// slots, this module exposes a generic, typed work queue used to dispatch
/// analysis jobs between the Axum request handlers and background workers without
/// ever blocking the async runtime on a channel send.
///
/// Design contract
/// ---------------
/// * `try_push` returns `Err(item)` immediately when the channel is at capacity
///   — the caller is responsible for converting this into a `503` response.
/// * `pop` performs a blocking receive and is intended for use on dedicated
///   `std::thread` workers spawned outside of the Tokio thread-pool.
/// * `try_pop` performs a non-blocking receive and returns `None` when the queue
///   is empty; useful for polling loops.
use crossbeam::channel::{bounded, Receiver, Sender, TrySendError};

/// A bounded, non-blocking work queue.
///
/// `T` is the unit of work (e.g. an analysis job descriptor).  The queue is
/// backed by a single crossbeam bounded channel so both ends are cheaply
/// clone-able and `Send + Sync`.
pub struct WorkQueue<T> {
    sender: Sender<T>,
    receiver: Receiver<T>,
    capacity: usize,
}

impl<T: Send> WorkQueue<T> {
    /// Create a new `WorkQueue` with the given maximum `capacity`.
    ///
    /// Panics if `capacity` is zero (a zero-capacity crossbeam channel is a
    /// rendezvous channel and does not satisfy the non-blocking contract).
    pub fn new(capacity: usize) -> Self {
        assert!(capacity > 0, "WorkQueue capacity must be > 0");
        let (sender, receiver) = bounded(capacity);
        Self {
            sender,
            receiver,
            capacity,
        }
    }

    /// Attempt to enqueue `item` without blocking.
    ///
    /// Returns `Ok(())` on success or `Err(item)` if the queue is full or
    /// the receiving end has been dropped, giving the caller back ownership
    /// of the rejected item so it can emit a `503` response immediately.
    pub fn try_push(&self, item: T) -> Result<(), T> {
        match self.sender.try_send(item) {
            Ok(()) => Ok(()),
            Err(TrySendError::Full(v)) | Err(TrySendError::Disconnected(v)) => Err(v),
        }
    }

    /// Block the **calling thread** until an item is available, then return it.
    ///
    /// Returns `None` only when every `Sender` clone has been dropped (i.e. the
    /// queue is permanently closed).  Call this from a `std::thread` worker, not
    /// from inside an async task — use `try_pop` or a `tokio::sync` primitive for
    /// async contexts.
    pub fn pop(&self) -> Option<T> {
        self.receiver.recv().ok()
    }

    /// Non-blocking dequeue.  Returns `None` immediately if the queue is empty.
    pub fn try_pop(&self) -> Option<T> {
        self.receiver.try_recv().ok()
    }

    /// Return a clone of the sender end, allowing multiple producers.
    pub fn sender(&self) -> Sender<T> {
        self.sender.clone()
    }

    /// Return a clone of the receiver end, allowing multiple consumers.
    pub fn receiver(&self) -> Receiver<T> {
        self.receiver.clone()
    }

    /// Maximum number of items the queue can hold before `try_push` starts
    /// returning `Err`.
    pub fn capacity(&self) -> usize {
        self.capacity
    }

    /// Number of items currently waiting in the queue.
    pub fn len(&self) -> usize {
        self.receiver.len()
    }

    /// `true` when no items are waiting.
    pub fn is_empty(&self) -> bool {
        self.receiver.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn push_and_pop_roundtrip() {
        let q: WorkQueue<u32> = WorkQueue::new(4);
        assert!(q.try_push(42).is_ok());
        assert_eq!(q.try_pop(), Some(42));
    }

    #[test]
    fn rejects_when_full() {
        let q: WorkQueue<u32> = WorkQueue::new(2);
        assert!(q.try_push(1).is_ok());
        assert!(q.try_push(2).is_ok());
        // Third push must be rejected and return the item back to the caller.
        let rejected = q.try_push(3).unwrap_err();
        assert_eq!(rejected, 3);
    }

    #[test]
    fn try_pop_empty_returns_none() {
        let q: WorkQueue<u32> = WorkQueue::new(4);
        assert_eq!(q.try_pop(), None);
    }

    #[test]
    fn len_and_capacity() {
        let q: WorkQueue<u32> = WorkQueue::new(8);
        assert_eq!(q.capacity(), 8);
        assert_eq!(q.len(), 0);
        q.try_push(1).unwrap();
        assert_eq!(q.len(), 1);
    }
}
