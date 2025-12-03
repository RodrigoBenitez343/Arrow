#!/usr/bin/env python3
"""
Performance test script to measure sequence loading and execution improvements.
"""

import time
import os
import sys
import json
from pathlib import Path

# Add the LoOper directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'LoOper'))

from LoOper.player.sequence_player import SequencePlayer, _sequence_cache


def measure_sequence_loading_performance():
    """
    Measure sequence loading performance with and without caching.
    """
    print("=== Sequence Loading Performance Test ===")
    
    # Find a test sequence file
    test_sequences = []
    sequences_dir = Path("LoOper/sequences")
    if sequences_dir.exists():
        test_sequences = list(sequences_dir.glob("*.json"))[:3]  # Test with first 3 sequences
    
    if not test_sequences:
        # Look for sequences in other common locations
        for pattern in ["**/*.json", "chains/**/*.json"]:
            test_sequences.extend(list(Path(".").glob(pattern))[:3])
    
    if not test_sequences:
        print("No test sequences found. Creating a mock sequence for testing.")
        mock_sequence = {
            "actions": [
                {"type": "click", "x": 100, "y": 100},
                {"type": "key", "key": "enter"},
                {"type": "wait", "duration": 0.1}
            ]
        }
        mock_file = Path("test_sequence.json")
        with open(mock_file, 'w') as f:
            json.dump(mock_sequence, f)
        test_sequences = [mock_file]
    
    results = {}
    
    for seq_file in test_sequences[:3]:  # Test with up to 3 sequences
        print(f"\nTesting sequence: {seq_file}")
        
        # Clear cache before testing
        _sequence_cache.clear()
        
        # Test cold loading (no cache)
        start_time = time.perf_counter()
        try:
            player1 = SequencePlayer(str(seq_file))
            cold_load_time = time.perf_counter() - start_time
            print(f"  Cold load time: {cold_load_time:.4f} seconds")
        except Exception as e:
            print(f"  Error loading sequence: {e}")
            continue
        
        # Test warm loading (with cache)
        start_time = time.perf_counter()
        try:
            player2 = SequencePlayer(str(seq_file))
            warm_load_time = time.perf_counter() - start_time
            print(f"  Warm load time: {warm_load_time:.4f} seconds")
        except Exception as e:
            print(f"  Error loading cached sequence: {e}")
            continue
        
        # Calculate improvement
        if cold_load_time > 0:
            improvement = ((cold_load_time - warm_load_time) / cold_load_time) * 100
            print(f"  Cache improvement: {improvement:.1f}%")
            
            results[str(seq_file)] = {
                'cold_load': cold_load_time,
                'warm_load': warm_load_time,
                'improvement': improvement
            }
    
    # Clean up mock file if created
    mock_file = Path("test_sequence.json")
    if mock_file.exists():
        mock_file.unlink()
    
    return results


def measure_initialization_performance():
    """
    Measure SequencePlayer initialization performance with lazy loading.
    """
    print("\n=== Initialization Performance Test ===")
    
    # Create a test sequence
    mock_sequence = {
        "actions": [
            {"type": "click", "x": 100, "y": 100},
            {"type": "key", "key": "enter"},
            {"type": "wait", "duration": 0.1}
        ] * 50  # Make it larger to see the difference
    }
    mock_file = Path("large_test_sequence.json")
    with open(mock_file, 'w') as f:
        json.dump(mock_sequence, f)
    
    try:
        # Test eager loading (traditional)
        start_time = time.perf_counter()
        player_eager = SequencePlayer(str(mock_file), lazy_load=False)
        eager_init_time = time.perf_counter() - start_time
        print(f"Eager initialization: {eager_init_time:.4f} seconds")
        
        # Test lazy loading
        start_time = time.perf_counter()
        player_lazy = SequencePlayer(str(mock_file), lazy_load=True)
        lazy_init_time = time.perf_counter() - start_time
        print(f"Lazy initialization: {lazy_init_time:.4f} seconds")
        
        # Calculate improvement
        if eager_init_time > 0:
            improvement = ((eager_init_time - lazy_init_time) / eager_init_time) * 100
            print(f"Lazy loading improvement: {improvement:.1f}%")
        
        return {
            'eager_init': eager_init_time,
            'lazy_init': lazy_init_time,
            'improvement': improvement if eager_init_time > 0 else 0
        }
    
    finally:
        # Clean up
        if mock_file.exists():
            mock_file.unlink()


def measure_cache_performance():
    """
    Measure cache performance with multiple sequence loads.
    """
    print("\n=== Cache Performance Test ===")
    
    # Create multiple test sequences
    test_files = []
    for i in range(5):
        mock_sequence = {
            "actions": [
                {"type": "click", "x": 100 + i * 10, "y": 100 + i * 10},
                {"type": "key", "key": "enter"},
                {"type": "wait", "duration": 0.1}
            ] * (10 + i * 5)  # Varying sizes
        }
        mock_file = Path(f"cache_test_sequence_{i}.json")
        with open(mock_file, 'w') as f:
            json.dump(mock_sequence, f)
        test_files.append(mock_file)
    
    try:
        # Clear cache
        _sequence_cache.clear()
        
        # Test loading multiple sequences without cache
        start_time = time.perf_counter()
        for seq_file in test_files:
            _sequence_cache.clear()  # Force reload each time
            player = SequencePlayer(str(seq_file))
        no_cache_time = time.perf_counter() - start_time
        print(f"Loading 5 sequences without cache: {no_cache_time:.4f} seconds")
        
        # Test loading multiple sequences with cache
        _sequence_cache.clear()
        start_time = time.perf_counter()
        for seq_file in test_files:
            player = SequencePlayer(str(seq_file))
        with_cache_time = time.perf_counter() - start_time
        print(f"Loading 5 sequences with cache: {with_cache_time:.4f} seconds")
        
        # Test repeated loading (should be very fast with cache)
        start_time = time.perf_counter()
        for _ in range(3):  # Load each sequence 3 more times
            for seq_file in test_files:
                player = SequencePlayer(str(seq_file))
        repeated_cache_time = time.perf_counter() - start_time
        print(f"Loading 5 sequences 3 times each (cached): {repeated_cache_time:.4f} seconds")
        
        # Calculate improvements
        if no_cache_time > 0:
            cache_improvement = ((no_cache_time - with_cache_time) / no_cache_time) * 100
            print(f"Cache improvement: {cache_improvement:.1f}%")
        
        print(f"Cache size after test: {_sequence_cache.size()} entries")
        
        return {
            'no_cache': no_cache_time,
            'with_cache': with_cache_time,
            'repeated_cache': repeated_cache_time,
            'cache_improvement': cache_improvement if no_cache_time > 0 else 0
        }
    
    finally:
        # Clean up
        for seq_file in test_files:
            if seq_file.exists():
                seq_file.unlink()


def main():
    """
    Run all performance tests and display results.
    """
    print("LoOper Sequence Loading Performance Test")
    print("=" * 50)
    
    # Run tests
    loading_results = measure_sequence_loading_performance()
    init_results = measure_initialization_performance()
    cache_results = measure_cache_performance()
    
    # Summary
    print("\n" + "=" * 50)
    print("PERFORMANCE SUMMARY")
    print("=" * 50)
    
    if loading_results:
        avg_improvement = sum(r['improvement'] for r in loading_results.values()) / len(loading_results)
        print(f"Average sequence loading improvement: {avg_improvement:.1f}%")
    
    if init_results:
        print(f"Initialization improvement: {init_results['improvement']:.1f}%")
    
    if cache_results:
        print(f"Multi-sequence cache improvement: {cache_results['cache_improvement']:.1f}%")
    
    print("\nOptimizations implemented:")
    print("✓ Sequence data caching with file modification tracking")
    print("✓ Optimized JSON loading with buffered reading")
    print("✓ Lazy loading support for SequencePlayer")
    print("✓ Reduced initial delays in sequence execution")
    print("✓ Skip initial delay option for faster loop execution")
    
    print(f"\nCache statistics: {_sequence_cache.size()} sequences cached")


if __name__ == "__main__":
    main()