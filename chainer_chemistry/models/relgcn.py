import chainer
from chainer import cuda, functions

import chainer_chemistry
from chainer_chemistry.config import MAX_ATOMIC_NUM
from chainer_chemistry.links import EmbedAtomID
from chainer_chemistry.links.graph_linear import GraphLinear


def rescale_adj(adj):
    xp = cuda.get_array_module(adj)
    num_neighbors = functions.sum(adj, axis=(1, 2))
    base = xp.ones(num_neighbors.shape, dtype=xp.float32)
    cond = num_neighbors.data != 0
    num_neighbors_inv = 1 / functions.where(cond, num_neighbors, base)
    return adj * functions.broadcast_to(
        num_neighbors_inv[:, None, None, :], adj.shape)


class RelGCNUpdate(chainer.Chain):
    """RelGUN submodule for update part

    Args:
        in_channels (int): input channel dimension
        out_channels (int): output channel dimension
        num_edge_type (int): number of types of edge
    """

    def __init__(self, in_channels, out_channels, num_edge_type=4):
        super(RelGCNUpdate, self).__init__()
        with self.init_scope():
            self.graph_linear_self = GraphLinear(in_channels, out_channels)
            self.graph_linear_edge = GraphLinear(
                in_channels, out_channels * num_edge_type)
        self.num_edge_type = num_edge_type
        self.in_ch = in_channels
        self.out_ch = out_channels

    def __call__(self, h, adj):
        """

        Args:
            h: (batchsize, num_nodes, in_channels)
            adj: (batchsize, num_edge_type, num_nodes, num_nodes)

        Returns:
            (batchsize, num_nodes, ch)

        """

        mb, node, ch = h.shape

        # --- self connection, apply linear function ---
        hs = self.graph_linear_self(h)
        # --- relational feature, from neighbor connection ---
        # Expected number of neighbors of a vertex
        # Since you have to divide by it, if its 0, you need to
        # arbitrarily set it to 1
        m = self.graph_linear_edge(h)
        m = functions.reshape(m, (mb, node, self.out_ch, self.num_edge_type))
        m = functions.transpose(m, (0, 3, 1, 2))
        # m: (batchsize, edge_type, node, ch)
        # hr: (batchsize, edge_type, node, ch)
        hr = functions.matmul(adj, m)
        # hr: (batchsize, node, ch)
        hr = functions.sum(hr, axis=1)
        return hs + hr


class RelGCNReadout(chainer.Chain):
    """RelGCN submodule for readout part

    Args:
        in_channels (int): dimension of feature vector associated to
            each atom (node)
        out_channels (int): output dimension of feature vector
            associated to each molecule (graph)
        nobias (bool): If ``True``, then this function does not use
            the bias.
    """

    def __init__(self, in_channels, out_channels, nobias=True):
        super(RelGCNReadout, self).__init__()
        with self.init_scope():
            self.sig_linear = chainer_chemistry.links.GraphLinear(
                in_channels, out_channels, nobias=nobias)
            self.tanh_linear = chainer_chemistry.links.GraphLinear(
                in_channels, out_channels, nobias=nobias)

    def __call__(self, h, x=None):
        """
        (implicit:
            N is number of edges, R is number of types of relations)
        Args:
            h: (batchsize, num_nodes, ch)
                N x F : Matrix of edges, each row is a molecule and
                each column is a feature.
                F_l is the number of features at layer l
                F_0, the input layer, feature is type of molecule.
                Softmaxed

            x: (batchsize, num_nodes, ch)

        Returns:
            (batchsize, ch)
            F_n : Graph level representation

        Notes:
            I think they just incorporate "no edge" as one of the
            categories of relations, i've made it a separate tensor
            just to simplify some implementation, might change later
        """
        if x is None:
            in_feat = h
        else:
            in_feat = functions.concat([h, x], axis=2)
        sig_feat = functions.sigmoid(self.sig_linear(in_feat))
        tanh_feat = functions.tanh(self.tanh_linear(in_feat))

        return functions.tanh(functions.sum(sig_feat * tanh_feat, axis=1))


class RelGCN(chainer.Chain):

    """Relational GCN (RelGCN)

    See: Michael Schlichtkrull+, \
        Modeling Relational Data with Graph Convolutional Networks. \
        March 2017. \
        `arXiv:1703.06103 <https://arxiv.org/abs/1703.06103>`

    Args:
        out_channels (int): dimension of output feature vector
        num_edge_type (int): number of types of edge
        ch_list (list): channels of each update layer
        n_atom_types (int): number of types of atoms
        input_type (str): type of input vector
        scale_adj (bool): If ``True``, then this network normalizes
            adjacency matrix
    """

    def __init__(self, out_channels=64, num_edge_type=4, ch_list=None,
                 n_atom_types=MAX_ATOMIC_NUM, input_type='int',
                 scale_adj=False):

        super(RelGCN, self).__init__()
        if ch_list is None:
            ch_list = [16, 128, 64]
        with self.init_scope():
            if input_type == 'int':
                self.embed = EmbedAtomID(out_size=ch_list[0],
                                         in_size=n_atom_types)
            elif input_type == 'float':
                self.embed = GraphLinear(None, ch_list[0])
            else:
                raise ValueError("[ERROR] Unexpected value input_type={}"
                                 .format(input_type))
            self.rgcn_convs = chainer.ChainList(*[
                RelGCNUpdate(ch_list[i], ch_list[i+1], num_edge_type)
                for i in range(len(ch_list)-1)])
            self.rgcn_readout = RelGCNReadout(ch_list[-1], out_channels)
        # self.num_relations = num_edge_type
        self.input_type = input_type
        self.scale_adj = scale_adj

    def __call__(self, x, adj):
        """

        Args:
            x: (batchsize, num_nodes, in_channels)
            adj: (batchsize, num_edge_type, num_nodes, num_nodes)

        Returns: (batchsize, out_channels)

        """
        if x.dtype == self.xp.int32:
            assert self.input_type == 'int'
        else:
            assert self.input_type == 'float'
        h = self.embed(x)  # (minibatch, max_num_atoms)
        if self.scale_adj:
            adj = rescale_adj(adj)
        for rgcn_conv in self.rgcn_convs:
            h = functions.tanh(rgcn_conv(h, adj))
        h = self.rgcn_readout(h)
        return h
