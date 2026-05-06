use crossbeam::channel::{bounded, Receiver, Sender};

pub struct BufferPool {
    slots: Receiver<BufferSlot>,
    sender: Sender<BufferSlot>,
}

pub struct BufferSlot {
    pub id: usize,
    pub memory: Vec<u8>,
    pool_sender: Option<Sender<BufferSlot>>,
}

impl BufferPool {
    pub fn new(max_slots: usize) -> Self {
        let (sender, receiver) = bounded(max_slots);
        
        for i in 0..max_slots {
            let slot = BufferSlot {
                id: i,
                memory: Vec::with_capacity(4 * 1024 * 1024), // 4MB
                pool_sender: None,
            };
            sender.send(slot).unwrap();
        }
        
        metrics::gauge!("parallax_free_slots").set(max_slots as f64);

        Self {
            slots: receiver,
            sender,
        }
    }

    pub fn acquire(&self) -> Option<BufferSlot> {
        match self.slots.try_recv() {
            Ok(mut slot) => {
                metrics::gauge!("parallax_free_slots").decrement(1.0);
                slot.pool_sender = Some(self.sender.clone());
                Some(slot)
            }
            Err(_) => None,
        }
    }
}

impl Drop for BufferSlot {
    fn drop(&mut self) {
        if let Some(sender) = self.pool_sender.take() {
            self.memory.clear();
            let replacement = BufferSlot {
                id: self.id,
                memory: std::mem::take(&mut self.memory),
                pool_sender: None,
            };
            if sender.try_send(replacement).is_ok() {
                metrics::gauge!("parallax_free_slots").increment(1.0);
            }
        }
    }
}
