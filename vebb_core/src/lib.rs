use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Serialize, Deserialize, Debug)]
struct DepthUpdate {
    #[serde(rename = "E")]
    event_time: i64,
    #[serde(rename = "s")]
    symbol: String,
    #[serde(rename = "U")]
    first_update_id: i64,
    #[serde(rename = "u")]
    final_update_id: i64,
    #[serde(rename = "pu")]
    prev_final_update_id: i64,
    #[serde(rename = "b")]
    bids: Vec<Vec<String>>,
    #[serde(rename = "a")]
    asks: Vec<Vec<String>>,
}

#[pyclass]
struct OrderBook {
    #[pyo3(get)]
    pub bids: BTreeMap<String, f64>,
    #[pyo3(get)]
    pub asks: BTreeMap<String, f64>,
    #[pyo3(get)]
    pub last_update_id: i64,
}

#[pymethods]
impl OrderBook {
    #[new]
    fn new() -> Self {
        OrderBook {
            bids: BTreeMap::new(),
            asks: BTreeMap::new(),
            last_update_id: 0,
        }
    }

    fn update(&mut self, json_str: &str) -> PyResult<()> {
        let update: DepthUpdate = serde_json::from_str(json_str)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("JSON parse error: {}", e)))?;

        for b in update.bids {
            let price = b[0].clone();
            let qty: f64 = b[1].parse().unwrap_or(0.0);
            if qty == 0.0 {
                self.bids.remove(&price);
            } else {
                self.bids.insert(price, qty);
            }
        }

        for a in update.asks {
            let price = a[0].clone();
            let qty: f64 = a[1].parse().unwrap_or(0.0);
            if qty == 0.0 {
                self.asks.remove(&price);
            } else {
                self.asks.insert(price, qty);
            }
        }

        self.last_update_id = update.final_update_id;
        Ok(())
    }

    fn calculate_obi(&self, depth: usize) -> f64 {
        let bid_vol: f64 = self.bids.values().rev().take(depth).sum();
        let ask_vol: f64 = self.asks.values().take(depth).sum();
        
        if bid_vol + ask_vol == 0.0 {
            return 0.0;
        }
        (bid_vol - ask_vol) / (bid_vol + ask_vol)
    }

    fn get_top_levels(&self, depth: usize) -> PyResult<(Vec<(String, f64)>, Vec<(String, f64)>)> {
        let top_bids = self.bids.iter()
            .rev()
            .take(depth)
            .map(|(p, q)| (p.clone(), *q))
            .collect();
            
        let top_asks = self.asks.iter()
            .take(depth)
            .map(|(p, q)| (p.clone(), *q))
            .collect();

        Ok((top_bids, top_asks))
    }
}

#[pymodule]
fn vebb_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<OrderBook>()?;
    Ok(())
}
