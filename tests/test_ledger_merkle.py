import hashlib
import unittest

from sealbook.ledger import merkle


def digest(data):
    return hashlib.sha256(data).digest()


class MerkleTests(unittest.TestCase):
    def setUp(self):
        self.hashes = [hashlib.sha256(bytes([i])).hexdigest() for i in range(9)]
        a, b, c, d, e, f, g, h, i = [
            digest(b"\x00" + bytes.fromhex(value)) for value in self.hashes
        ]

        def node(left, right):
            return digest(b"\x01" + left + right)

        ab, cd, ef, gh = node(a, b), node(c, d), node(e, f), node(g, h)
        abcd, efgh = node(ab, cd), node(ef, gh)
        self.roots = [
            a,
            ab,
            node(ab, c),
            abcd,
            node(abcd, e),
            node(abcd, ef),
            node(abcd, node(ef, g)),
            node(abcd, efgh),
            node(node(abcd, efgh), i),
        ]
        self.proof_6_of_7 = [ef.hex(), abcd.hex()]

    def test_small_trees_and_all_leaf_positions(self):
        for size, expected in enumerate(self.roots, 1):
            hashes = self.hashes[:size]
            self.assertEqual(merkle.root(hashes), expected.hex())
            for index, leaf in enumerate(hashes):
                proof = merkle.inclusion_proof(hashes, index)
                self.assertTrue(
                    merkle.verify_inclusion(leaf, index, size, proof, expected.hex())
                )
        self.assertEqual(merkle.inclusion_proof(self.hashes[:7], 6), self.proof_6_of_7)

    def test_empty_tree(self):
        self.assertEqual(merkle.root([]), hashlib.sha256(b"").hexdigest())
        with self.assertRaises(ValueError):
            merkle.inclusion_proof([], 0)

    def test_tampered_and_malformed_proofs(self):
        hashes = self.hashes[:7]
        proof = merkle.inclusion_proof(hashes, 6)
        root = merkle.root(hashes)
        for path in [
            proof[::-1],
            proof[:-1],
            proof + [root],
            ["00" * 32, proof[1]],
            ["zz"],
        ]:
            self.assertFalse(merkle.verify_inclusion(hashes[6], 6, 7, path, root))
        for index, size in [(-1, 7), (7, 7), (0, 0), (True, 7), (0, 1.5)]:
            self.assertFalse(
                merkle.verify_inclusion(hashes[6], index, size, proof, root)
            )
        self.assertFalse(merkle.verify_inclusion("aa", 6, 7, proof, root))
        with self.assertRaises(ValueError):
            merkle.root(["aa"])
        with self.assertRaises(ValueError):
            merkle.inclusion_proof(hashes, -1)


if __name__ == "__main__":
    unittest.main()
