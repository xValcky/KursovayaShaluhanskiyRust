pub mod catalog;
pub mod engine;
pub mod model;

/// Простой генератор случайных анкет для замера скорости (без внешних зависимостей).
pub mod bench {
    use crate::engine::Survey;

    pub struct Rng(u64);
    impl Rng {
        pub fn new(seed: u64) -> Self {
            Rng(seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407) | 1)
        }
        pub fn uniform(&mut self) -> f64 {
            // xorshift64*
            self.0 ^= self.0 >> 12;
            self.0 ^= self.0 << 25;
            self.0 ^= self.0 >> 27;
            (self.0.wrapping_mul(2685821657736338717) >> 11) as f64 / (1u64 << 53) as f64
        }
        fn int(&mut self, n: usize) -> f64 {
            ((self.uniform() * n as f64) as usize).min(n - 1) as f64
        }
    }

    pub fn random_survey(r: &mut Rng) -> Survey {
        let budget = (8_000.0 * (25.0f64).powf(r.uniform())).round();
        Survey {
            budget_rub: budget,
            budget_min: if r.uniform() < 0.3 { (budget * 0.4).round() } else { 0.0 },
            strict_range: 0.0,
            imp_camera: r.int(11), imp_active: r.int(11), imp_standby: r.int(11), imp_perf: r.int(11),
            imp_compact: r.int(11), imp_charge: r.int(11), imp_storage: r.int(11), imp_new: r.int(11),
            imp_value: r.int(11), imp_rugged: r.int(4),
            gaming: r.int(2), photo: r.int(2), work: r.int(2),
            os_pref: r.int(3), need_nfc: r.int(2), esim_pref: r.int(3), ir_pref: r.int(3),
            screen_pref: r.int(5),
            min_hz: [0.0, 90.0, 120.0, 144.0][r.int(4) as usize],
            min_rom: [64.0, 128.0, 256.0, 512.0][r.int(4) as usize],
            ..Default::default()
        }
    }
}
