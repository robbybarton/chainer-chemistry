import numpy
import pytest
from rdkit import Chem

from chainer_chemistry.dataset.parsers import SmilesParser
from chainer_chemistry.dataset.preprocessors import NFPPreprocessor


@pytest.fixture
def mol():
    ret = Chem.MolFromSmiles('CN=C=O')
    return ret


@pytest.fixture
def pp():
    return NFPPreprocessor()


def test_nfp_preprocessor(mol, pp):
    ret = pp.get_input_features(mol)
    assert len(ret) == 2
    actual_atom_array, actual_adj_array = ret

    expect_atom_array = numpy.array([6, 7, 6, 8], dtype=numpy.int32)
    numpy.testing.assert_array_equal(actual_atom_array, expect_atom_array)

    expect_adj_array = numpy.array([[1, 1, 0, 0],
                                    [1, 1, 1, 0],
                                    [0, 1, 1, 1],
                                    [0, 0, 1, 1]], dtype=numpy.float32)
    numpy.testing.assert_array_equal(actual_adj_array, expect_adj_array)


def test_nfp_preprocessor_default():
    preprocessor = NFPPreprocessor()

    dataset = SmilesParser(preprocessor).parse(
        ['C#N', 'Cc1cnc(C=O)n1C', 'c1ccccc1'])['dataset']

    index = numpy.random.choice(len(dataset), None)
    atoms, adjs = dataset[index]

    assert atoms.ndim == 1  # (atom, )
    assert atoms.dtype == numpy.int32
    # (atom from, atom to)
    assert adjs.ndim == 2
    assert adjs.dtype == numpy.float32


def test_nfp_preprocessor_assert_raises():
    with pytest.raises(ValueError):
        pp = NFPPreprocessor(max_atoms=3, out_size=2)  # NOQA


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
